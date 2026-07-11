"""
CreatureAIHandler v2.0 — обработчик диалогов с NPC и ботами
Новое: интегрирован MessageClassifier для разделения тактики и болтовни

Архитектура (три слоя, как у дирижёра):
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Классификатор │ ──► │   LLM-диалог    │     │  Планировщик    │
│  (что сказали?) │     │  (как ответить) │     │ (что делать?)   │
│   quick_check   │     │   build_prompt  │     │  (ЭТАП 3)       │
└─────────────────┘     └─────────────────┘     └─────────────────┘
        │                                              │
        └──────────────► CHAT ────────────────────────┘
        │                      (просто болтаем)
        └──────────────► TACTIC ─────────────────────►
                               (подтверждаем + ждём план)

Если классификатор говорит TACTIC — бот отвечает кратко "Принято"
и готовится к получению детального плана (Этап 3).
Если CHAT — работает старая система диалогов через LLM.
"""

import time
import json
import hashlib
import threading
import logging
from typing import Dict, Tuple, Optional

import config
from core.world_state import WorldState
from core.llm_queue import PriorityLLMQueue
from core.event_bus import EventBus
from wow_connector.db_bridge import WoWDBBridge
from modules.creature_ai import prompts
from modules.creature_ai import validators

# ═══════════════════════════════════════════════════════════════════
# НОВОЕ: Импорт классификатора (Этап 2)
# ═══════════════════════════════════════════════════════════════════
try:
    from modules.tactical_ai.classifier import MessageClassifier
    TACTICAL_AI_AVAILABLE = True
except ImportError:
    TACTICAL_AI_AVAILABLE = False
    logging.warning("TacticalAI not found, tactical commands will be treated as chat")

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# НОВОЕ (Этап 6): Загрузчик ролевых шаблонов
# ═══════════════════════════════════════════════════════════════════
try:
    from modules.creature_ai.persona_loader import get_persona_loader
    PERSONA_SYSTEM_AVAILABLE = True
except ImportError:
    PERSONA_SYSTEM_AVAILABLE = False
    logging.warning("PersonaLoader not found, using default responses")

class CreatureAIHandler:
    def __init__(self, world_state: WorldState, llm_queue: PriorityLLMQueue,
                 event_bus: EventBus, db_bridge: WoWDBBridge):
        self.world = world_state
        self.llm = llm_queue
        self.bus = event_bus
        self.db = db_bridge
        
        # FIX: кэш по (player_guid, hash_текста), а не только по npc_guid.
        self._last_talk: Dict[int, float] = {}  # player_guid -> timestamp
        self._cache: Dict[Tuple[int, str], dict] = {}  # (player_guid, text_hash) -> response
        self._cache_ttl = 3.0  # секунды
        
        # ═══════════════════════════════════════════════════════════
        # НОВОЕ: Инициализация классификатора (Этап 2)
        # ═══════════════════════════════════════════════════════════
        if TACTICAL_AI_AVAILABLE:
            self.classifier = MessageClassifier(llm_queue)
            logger.info("TacticalAI classifier loaded")
        else:
            self.classifier = None
            logger.warning("TacticalAI classifier NOT loaded")
        
        # ═══════════════════════════════════════════════════════════
        # НОВОЕ (Этап 6): Инициализация системы ролей
        # ═══════════════════════════════════════════════════════════
        if PERSONA_SYSTEM_AVAILABLE:
            self.persona_loader = get_persona_loader()
            logger.info("Persona system loaded. Active: %s",
                       self.persona_loader.get_current_persona_name())
        else:
            self.persona_loader = None
        
        # Подписаться на события от DB Bridge
        self.db.register_callback(self._on_chat_request)
        
        logger.info("CreatureAIHandler v2.0 initialized")
    
    @staticmethod
    def _text_hash(text: str) -> str:
        """FIX: хэш текста сообщения для кэша."""
        return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]
    
    # ═══════════════════════════════════════════════════════════════
    # ГЛАВНЫЙ ОБРАБОТЧИК: входящий запрос из игры
    # ═══════════════════════════════════════════════════════════════
    def _on_chat_request(self, request: dict):
        """
        Вызывается когда DB Bridge находит новый запрос из игры.
        
        Теперь с классификацией:
        1. Определяем тип сообщения (тактика/болтовня)
        2. Если тактика — быстрое подтверждение (Этап 2)
        3. Если болтовня — полный LLM-диалог (как раньше)
        """
        req_id = request.get("id", 0)
        npc_guid = request["npc_guid"]
        npc_entry = request.get("npc_entry", 0)
        npc_name = request.get("npc_name", "Unknown")
        player_name = request["player_name"]
        player_guid = request.get("player_guid", 0)
        message = request["message"]
        channel = request.get("channel_type", "SAY")
        is_player = request.get("target_is_player", False)
        
        logger.info("Incoming: %s '%s' → %s (channel=%s, is_player=%s)",
                   player_name, message[:50], npc_name, channel, is_player)
        
        # ═══════════════════════════════════════════════════════════
        # НОВОЕ: Классификация сообщения (только для ботов)
        # ═══════════════════════════════════════════════════════════
        if is_player and self.classifier and channel in ("SAY-BOT", "WHISPER", "PARTY"):
            # Убираем префикс [FILTER] из сообщения (добавлен в Lua)
            clean_msg = message
            if "] " in message:
                clean_msg = message.split("] ", 1)[1]
            
            # Классифицируем: тактика или болтовня?
            classification = self.classifier.classify_with_fallback(
                message=clean_msg,
                player_name=player_name,
                channel=channel,
                bot_count=1
            )
            
            msg_type = classification.get("type", "CHAT")
            confidence = classification.get("confidence", 0.0)
            
            logger.info("Classified '%s' → %s (%.2f): %s",
                       clean_msg[:40], msg_type, confidence,
                       classification.get("reason", ""))
            
            # Если тактика — обрабатываем по-другому
            if msg_type == "TACTIC" and confidence >= 0.5:
                self._handle_tactic_request(request, clean_msg, classification)
                return
            # Если MIXED — тоже тактика, но с болтовнёй
            elif msg_type == "MIXED" and confidence >= 0.5:
                self._handle_mixed_request(request, clean_msg, classification)
                return
            # Иначе CHAT — идём дальше к обычному диалогу
        
        # ═══════════════════════════════════════════════════════════
        # СТАРЫЙ ПУТЬ: обычный диалог (NPC или CHAT-бот)
        # ═══════════════════════════════════════════════════════════
        self._handle_chat_dialog(request, message, is_player, channel)
    
    # ═══════════════════════════════════════════════════════════════
    # НОВЫЙ МЕТОД: Обработка тактической команды через LLM (Этап 6)
    # ═══════════════════════════════════════════════════════════════
    def _handle_tactic_request(self, request: dict, clean_msg: str,
                                classification: dict) -> None:
        """
        Обработать тактическую команду.

        Этап 6: Генерируем подтверждение ЧЕРЕЗ LLM с учётом роли персонажа.
        Быстро (max_tokens=40), но живо и в стиле.
        """
        player_guid = request.get("player_guid", 0)
        npc_guid = request["npc_guid"]
        npc_entry = request.get("npc_entry", 0)
        npc_name = request.get("npc_name", "Bot")
        player_name = request.get("player_name", "Player")

        logger.info("TACTIC from %s to %s: '%s'", player_name, npc_name, clean_msg)

        # Получаем ролевой контекст
        persona = self._get_bot_persona(npc_guid, npc_name)

        # Формируем системный промпт для быстрого подтверждения
        system_prompt = self._build_ack_system_prompt(persona, "tactic")

        # Пользовательский промпт — контекст команды
        user_prompt = f"""Лидер группы ({player_name}) дал тактическую команду: "{clean_msg}"

Твоя задача: КРАТКО подтвердить получение команды (1-2 предложения).
Отвечай В СТИЛЕ своего персонажа. Можно с эмоциями, сленгом, шутками.

Формат ответа (ТОЛЬКО JSON):
{{
  "speech": "текст подтверждения",
  "emote_id": 0
}}"""

        # Отправляем в LLM (быстро, priority=1)
        future = self.llm.submit(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.8,
            max_tokens=60,
            priority=1
        )

        # Обрабатываем асинхронно
        threading.Thread(
            target=self._process_tactic_ack,
            args=(future, player_guid, npc_guid, npc_entry, npc_name,
                  player_name, clean_msg, classification),
            daemon=True
        ).start()

    def _handle_mixed_request(self, request: dict, clean_msg: str,
                               classification: dict) -> None:
        """
        Обработать смешанное сообщение (тактика + болтовня).

        Этап 6: Тоже через LLM, но с более живым, разговорным стилем.
        """
        player_guid = request.get("player_guid", 0)
        npc_guid = request["npc_guid"]
        npc_entry = request.get("npc_entry", 0)
        npc_name = request.get("npc_name", "Bot")
        player_name = request.get("player_name", "Player")

        logger.info("MIXED from %s to %s: '%s'", player_name, npc_name, clean_msg)

        persona = self._get_bot_persona(npc_guid, npc_name)
        system_prompt = self._build_ack_system_prompt(persona, "mixed")

        user_prompt = f"""Лидер группы ({player_name}) сказал: "{clean_msg}"

Это И тактика, И болтовня. Ответь живо, по-человечески.
Подтверди команду, но можешь пошутить, поболтать немного.

Формат (ТОЛЬКО JSON):
{{
  "speech": "ответ",
  "emote_id": 0,
  "mood_change": "0"
}}"""

        future = self.llm.submit(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.85,
            max_tokens=80,
            priority=1
        )

        threading.Thread(
            target=self._process_tactic_ack,
            args=(future, player_guid, npc_guid, npc_entry, npc_name,
                  player_name, clean_msg, classification),
            daemon=True
        ).start()

    # ═══════════════════════════════════════════════════════════════
    # ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ (Этап 6)
    # ═══════════════════════════════════════════════════════════════

    def _get_bot_persona(self, bot_guid: int, bot_name: str) -> dict:
        """
        Получить ролевой шаблон для конкретного бота.

        Сначала смотрим WorldState (может быть персональная настройка),
        потом берём глобальную persona.
        """
        # Пробуем получить из WorldState (персональная persona бота)
        entity = self.world.get_nested(f"entities.{bot_guid}", {})
        persona_name = entity.get("persona")

        if persona_name and self.persona_loader:
            return self.persona_loader.get_persona(persona_name)

        # Глобальная persona по умолчанию
        if self.persona_loader:
            return self.persona_loader.get_persona()

        # Fallback
        return {
            "name": "Default",
            "system_prompt_addendum": "Ты — игрок-бот в WoW.",
            "voice_examples": ["Понял.", "Готово."]
        }

    def _build_ack_system_prompt(self, persona: dict, context: str) -> str:
        """
        Собрать системный промпт для подтверждения с учётом persona.

        Аргументы:
            persona: dict с ролевым шаблоном
            context: "tactic" или "mixed"
        """
        base = f"""Ты — игрок-бот в World of Warcraft (Wrath of the Lich King).

ИМЯ: {persona.get('name', 'Бот')}
СТИЛЬ: {persona.get('system_prompt_addendum', '')}

ПРАВИЛА:
1. Отвечай КРАТКО (максимум 2 предложения).
2. Соблюдай СТИЛЬ персонажа (сленг, лор, сарказм — что указано выше).
3. Это подтверждение ТАКТИЧЕСКОЙ КОМАНДЫ — не надо длинных речей.
4. Только JSON формат:

{{
  "speech": "текст",
  "emote_id": 0
}}

ЭМОЦИИ: 0=нет, 1=talk, 3=wave, 14=rude, 18=cry, 25=point"""

        # Добавляем примеры голоса для вдохновения
        examples = persona.get("voice_examples", [])
        if examples:
            base += "\n\nПРИМЕРЫ ТВОЕЙ РЕЧИ:\n"
            for ex in examples[:3]:
                base += f'- "{ex}"\n'

        return base

    def _process_tactic_ack(self, future, player_guid, npc_guid, npc_entry,
                            npc_name, player_name, clean_msg, classification):
        """
        Обработать ответ LLM на подтверждение тактики.

        Этот метод работает в отдельном потоке.
        """
        try:
            result = future.result(timeout=10)

            # Парсим JSON из ответа
            speech = "Понял."  # fallback
            emote_id = 0

            content = result.get("speech", "") if isinstance(result, dict) else str(result)

            # Пытаемся найти JSON
            if isinstance(content, str):
                try:
                    # Ищем JSON в тексте
                    start = content.find("{")
                    end = content.rfind("}")
                    if start != -1 and end != -1:
                        data = json.loads(content[start:end+1])
                        speech = data.get("speech", speech)[:255]  # лимит WoW
                        emote_id = int(data.get("emote_id", 0))
                except (json.JSONDecodeError, ValueError):
                    # Если не JSON — используем текст как есть (обрезанный)
                    speech = content[:255]

            response = {
                "speech": speech,
                "emote_id": emote_id,
                "action_command": "tactic_acknowledged",
                "mood_change": "0",
                "set_flag": None,
            }

            # Отправляем ответ в игру
            self._send_response(player_guid, npc_guid, npc_entry, response, True)

            # Публикуем событие для планировщика
            self.bus.publish("tactic_command_received", {
                "player_guid": player_guid,
                "player_name": player_name,
                "bot_guid": npc_guid,
                "bot_name": npc_name,
                "command": clean_msg,
                "classification": classification,
                "timestamp": time.time(),
            })

            logger.info("TACTIC ack from %s: '%s...'", npc_name, speech[:50])

        except Exception as e:
            logger.error("Tactic ack failed: %s", e)
            # Fallback: отправляем стандартное подтверждение
            fallback = {
                "speech": "Понял, выполняю.",
                "emote_id": 25,
                "action_command": "tactic_acknowledged",
                "mood_change": "0",
                "set_flag": None,
            }
            self._send_response(player_guid, npc_guid, npc_entry, fallback, True)
    # ═══════════════════════════════════════════════════════════════
    # СТАРЫЙ МЕТОД: Обычный диалог (переименован для ясности)
    # ═══════════════════════════════════════════════════════════════
    def _handle_chat_dialog(self, request: dict, message: str, 
                            is_player: bool, channel: str) -> None:
        """
        Обычный диалог с NPC или болтовня с ботом.
        Это ваша старая логика, работает как раньше.
        """
        req_id = request.get("id", 0)
        npc_guid = request["npc_guid"]
        npc_entry = request.get("npc_entry", 0)
        npc_name = request.get("npc_name", "Unknown")
        player_name = request["player_name"]
        player_guid = request.get("player_guid", 0)
        
        # FIX: Rate limit по player_guid + hash текста
        now = time.time()
        text_hash = self._text_hash(message)
        cache_key = (player_guid, text_hash)
        
        if now - self._last_talk.get(player_guid, 0) < self._cache_ttl:
            cached = self._cache.get(cache_key)
            if cached:
                logger.debug("Cache hit for player %d, hash %s", player_guid, text_hash)
                self._send_response(player_guid, npc_guid, npc_entry, cached, is_player)
                return
        
        # Обновить/создать данные NPC/бота в WorldState
        self._ensure_entity_exists(npc_guid, npc_name, npc_entry, is_player)
        
        # Получить контекст
        ctx = self.world.get_full_context(str(npc_guid))
        entity_data = ctx.get("npc", {})
        
        # Данные игрока
        player_data = {
            "name": player_name,
            "guid": player_guid,
            "race": "Unknown",
            "class": "Unknown",
            "reputation": entity_data.get("reputation_to_player", 0),
        }
        
        # Выбрать промпт в зависимости от типа
        if is_player:
            system_prompt = prompts.build_bot_system_prompt(entity_data, ctx, player_data, channel)
        else:
            system_prompt = prompts.build_system_prompt(entity_data, ctx, player_data)
        
        user_prompt = prompts.build_user_prompt(message, channel, is_player)
        
        # Приоритет: PARTY/WHISPER/SAY-BOT = 1 (микро, быстрый ответ), SAY = 2 (мезо)
        priority = 1 if channel in ("PARTY", "WHISPER", "SAY-BOT") else 2
        
        # Отправить в LLM
        future = self.llm.submit(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.75,
            max_tokens=150 if is_player else 120,
            priority=priority,
        )
        
        # Обработать результат в отдельном потоке
        threading.Thread(
            target=self._process_llm_response,
            args=(future, req_id, player_guid, npc_guid, npc_entry, 
                  player_name, message, is_player, channel),
            daemon=True,
        ).start()
    
    def _process_llm_response(self, future, req_id, player_guid, npc_guid, 
                             npc_entry, player_name, message, is_player, channel):
        """Обработать ответ LLM (старая логика, без изменений)."""
        try:
            result = future.result(timeout=15)
        except Exception as e:
            logger.error("LLM request failed for %s (guid=%d): %s", 
                        "bot" if is_player else "NPC", npc_guid, e)
            result = validators._fallback_response("bot" if is_player else "NPC")
        
        validated = validators.validate_response(result, "bot" if is_player else "NPC")
        self._update_entity_state(npc_guid, validated, message, is_player)
        self._send_response(player_guid, npc_guid, npc_entry, validated, is_player)
        
        text_hash = self._text_hash(message)
        cache_key = (player_guid, text_hash)
        self._last_talk[player_guid] = time.time()
        self._cache[cache_key] = validated
        
        self.bus.publish("entity_talk_ended", {
            "npc_guid": npc_guid,
            "is_player": is_player,
            "player_name": player_name,
            "player_input": message,
            "channel": channel,
            "response": validated,
        })
        
        logger.info("%s %d responded in %s: '%s'", 
                   "Bot" if is_player else "NPC", npc_guid, channel, validated["speech"][:50])
    
    def _send_response(self, player_guid: int, npc_guid: int, npc_entry: int, 
                       response: dict, is_player: bool):
        """Записать ответ в ai_responses для Lua."""
        logger.debug("Sending response: player=%d, npc=%d, is_player=%s", 
                    player_guid, npc_guid, is_player)
        
        self.db.write_response(
            player_guid=player_guid,
            npc_guid=npc_guid,
            npc_entry=npc_entry,
            response_text=response["speech"],
            emote_id=response.get("emote_id", 0),
            action_command=response.get("action_command"),
            mood_change=response.get("mood_change", "0"),
        )
    
    def _ensure_entity_exists(self, guid: int, name: str, entry: int, is_player: bool):
        """Создать запись в WorldState если нет."""
        path = f"entities.{guid}"
        existing = self.world.get_nested(path)
        if not existing:
            default_data = {
                "name": name,
                "guid": guid,
                "entry": entry,
                "is_player": is_player,
                "role": "Боец" if is_player else "Житель",
                "trait": "Агрессивный" if is_player else "Обычный",
                "mood": "нейтральный",
                "mood_score": 0,
                "faction": "Horde" if is_player else "Stormwind",
                "reputation_to_player": 0,
                "memory": [],
                "dialogue_count": 0,
                "last_channel": "SAY",
            }
            self.world.set_nested(path, default_data)
            logger.debug("Created WorldState for %s %d (path=%s)",
                        "bot" if is_player else "NPC", guid, path)
    
    def _update_entity_state(self, guid: int, response: dict, player_message: str, is_player: bool):
        """Обновить состояние после диалога."""
        path = f"entities.{guid}"
        
        mood_change = 0
        try:
            mood_change = int(response.get("mood_change", 0))
        except (ValueError, TypeError):
            pass
        
        current_mood = self.world.get_nested(f"{path}.mood_score", 0)
        new_mood = max(-100, min(100, current_mood + mood_change))
        self.world.set_nested(f"{path}.mood_score", new_mood)
        
        if new_mood > 30:
            mood_text = "дружелюбный"
        elif new_mood < -30:
            mood_text = "враждебный"
        else:
            mood_text = "нейтральный"
        self.world.set_nested(f"{path}.mood", mood_text)
        
        memory = self.world.get_nested(f"{path}.memory", [])
        memory.append({
            "player_msg": player_message[:100],
            "ai_reply": response.get("speech", "")[:100],
            "timestamp": time.strftime("%H:%M:%S"),
        })
        if len(memory) > 10:
            memory = memory[-10:]
        self.world.set_nested(f"{path}.memory", memory)
        
        count = self.world.get_nested(f"{path}.dialogue_count", 0)
        self.world.set_nested(f"{path}.dialogue_count", count + 1)
        
        self.world.append_chronology(
            f"{self.world.get_nested('meta.world_hour', 12)}:00 — "
            f"{'Bot' if is_player else 'NPC'} {guid} talked with player"
        )
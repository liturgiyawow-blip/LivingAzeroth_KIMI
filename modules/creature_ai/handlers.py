"""
CreatureAIHandler — обработчик диалогов с NPC и ботами (Player)
Поддерживает: SAY (NPC), PARTY (боты), WHISPER (боты)
"""

import time
import hashlib
import threading
import logging
from typing import Dict, Tuple

import config
from core.world_state import WorldState
from core.llm_queue import PriorityLLMQueue
from core.event_bus import EventBus
from wow_connector.db_bridge import WoWDBBridge
from modules.creature_ai import prompts
from modules.creature_ai import validators

logger = logging.getLogger(__name__)


class CreatureAIHandler:
    def __init__(self, world_state: WorldState, llm_queue: PriorityLLMQueue,
                 event_bus: EventBus, db_bridge: WoWDBBridge):
        self.world = world_state
        self.llm = llm_queue
        self.bus = event_bus
        self.db = db_bridge
        
        # FIX: кэш по (player_guid, hash_текста), а не только по npc_guid.
        # Иначе при спаме NPC отвечал старым ответом на новый вопрос.
        self._last_talk: Dict[int, float] = {}  # player_guid -> timestamp
        self._cache: Dict[Tuple[int, str], dict] = {}  # (player_guid, text_hash) -> response
        self._cache_ttl = 3.0  # секунды
        
        # Подписаться на события от DB Bridge
        self.db.register_callback(self._on_chat_request)
        
        logger.info("CreatureAIHandler initialized")
    
    @staticmethod
    def _text_hash(text: str) -> str:
        """FIX: хэш текста сообщения для кэша."""
        return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]
    
    def _on_chat_request(self, request: dict):
        """Вызывается когда DB Bridge находит новый запрос из игры"""
        req_id = request.get("id", 0)
        npc_guid = request["npc_guid"]
        npc_entry = request.get("npc_entry", 0)
        npc_name = request.get("npc_name", "Unknown")
        player_name = request["player_name"]
        player_guid = request.get("player_guid", 0)
        message = request["message"]
        channel = request.get("channel_type", "SAY")
        is_player = request.get("target_is_player", False)
        
        # FIX: Rate limit по player_guid + hash текста, не только по npc_guid.
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
        
        # Получить контекст (теперь ищет по GUID в entities)
        ctx = self.world.get_full_context(str(npc_guid))
        entity_data = ctx.get("npc", {})
        
        # Данные игрока
        player_data = {
            "name": player_name,
            "guid": player_guid,
            "race": "Unknown",  # TODO: расширить через DB query
            "class": "Unknown",
            "reputation": entity_data.get("reputation_to_player", 0),
        }
        
        # Выбрать промпт в зависимости от типа (NPC vs Bot)
        if is_player:
            system_prompt = prompts.build_bot_system_prompt(entity_data, ctx, player_data, channel)
        else:
            system_prompt = prompts.build_system_prompt(entity_data, ctx, player_data)
        
        user_prompt = prompts.build_user_prompt(message, channel, is_player)
        
        # Приоритет: PARTY/WHISPER = 1 (микро, быстрый ответ), SAY = 2 (мезо)
        priority = 1 if channel in ("PARTY", "WHISPER") else 2
        
        # Отправить в LLM
        future = self.llm.submit(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.75,
            max_tokens=150 if is_player else 120,  # боты могут чуть длиннее
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
        try:
            result = future.result(timeout=15)
        except Exception as e:
            logger.error("LLM request failed for %s (guid=%d): %s", 
                        "bot" if is_player else "NPC", npc_guid, e)
            result = validators._fallback_response("bot" if is_player else "NPC")
        
        # Валидация
        validated = validators.validate_response(result, "bot" if is_player else "NPC")
        
        # Обновить WorldState
        self._update_entity_state(npc_guid, validated, message, is_player)
        
        # Записать ответ в БД (Lua прочитает и заставит говорить)
        self._send_response(player_guid, npc_guid, npc_entry, validated, is_player)
        
        # FIX: кэш по (player_guid, text_hash)
        text_hash = self._text_hash(message)
        cache_key = (player_guid, text_hash)
        self._last_talk[player_guid] = time.time()
        self._cache[cache_key] = validated
        
        # Опубликовать событие
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
        """Записать ответ в ai_responses для Lua"""
        logger.debug("Sending response: player=%d, npc=%d, is_player=%s", 
                    player_guid, npc_guid, is_player)
        
        # FIX: action_command и mood_change теперь передаются в БД.
        # Раньше action_command терялся — Lua никогда не видел команду.
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
        """Создать запись в WorldState если нет"""
        # FIX: пишем в entities.{guid} — туда смотрит get_full_context()
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
        """Обновить состояние после диалога"""
        path = f"entities.{guid}"
        
        # Изменить настроение
        mood_change = 0
        try:
            mood_change = int(response.get("mood_change", 0))
        except (ValueError, TypeError):
            pass
        
        current_mood = self.world.get_nested(f"{path}.mood_score", 0)
        new_mood = max(-100, min(100, current_mood + mood_change))
        self.world.set_nested(f"{path}.mood_score", new_mood)
        
        # Обновить текст настроения
        if new_mood > 30:
            mood_text = "дружелюбный"
        elif new_mood < -30:
            mood_text = "враждебный"
        else:
            mood_text = "нейтральный"
        self.world.set_nested(f"{path}.mood", mood_text)
        
        # Запомнить диалог
        memory = self.world.get_nested(f"{path}.memory", [])
        memory.append({
            "player_msg": player_message[:100],
            "ai_reply": response.get("speech", "")[:100],
            "timestamp": time.strftime("%H:%M:%S"),
        })
        if len(memory) > 10:
            memory = memory[-10:]
        self.world.set_nested(f"{path}.memory", memory)
        
        # Счётчик
        count = self.world.get_nested(f"{path}.dialogue_count", 0)
        self.world.set_nested(f"{path}.dialogue_count", count + 1)
        
        # Хронология мира
        self.world.append_chronology(
            f"{self.world.get_nested('meta.world_hour', 12)}:00 — "
            f"{'Bot' if is_player else 'NPC'} {guid} talked with player"
        )
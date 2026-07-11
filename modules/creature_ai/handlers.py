"""
CreatureAIHandler v3.0 — обработчик диалогов с NPC

ВАРИАНТ А: "Живые NPC"
- Убрана вся тактическая логика (была для ботов)
- Фокус: диалоги, память, репутация, квесты
- Работает ТОЛЬКО с NPC (target_is_player=False)
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

try:
    from modules.creature_ai.persona_loader import get_persona_loader
    PERSONA_SYSTEM_AVAILABLE = True
except ImportError:
    PERSONA_SYSTEM_AVAILABLE = False
    logging.warning("PersonaLoader not found, using default responses")

logger = logging.getLogger(__name__)


class CreatureAIHandler:
    """
    Главный обработчик диалогов с NPC.

    Как "диспетчер бесед": получает запрос от игрока,
    собирает контекст (кто NPC, что помнит, какая репутация),
    отправляет в LLM, получает ответ, записывает в БД для Lua.
    """

    def __init__(self, world_state: WorldState, llm_queue: PriorityLLMQueue,
                 event_bus: EventBus, db_bridge: WoWDBBridge):
        self.world = world_state
        self.llm = llm_queue
        self.bus = event_bus
        self.db = db_bridge

        # Кэш ответов: (player_guid, hash_текста) → ответ
        self._last_talk: Dict[int, float] = {}  # player_guid → timestamp
        self._cache: Dict[Tuple[int, str], dict] = {}
        self._cache_ttl = 3.0  # секунды

        # Загрузчик ролей
        if PERSONA_SYSTEM_AVAILABLE:
            self.persona_loader = get_persona_loader()
            logger.info("Persona system loaded. Active: %s",
                       self.persona_loader.get_current_persona_name())
        else:
            self.persona_loader = None

        # Подписаться на события от DB Bridge
        self.db.register_callback(self._on_chat_request)

        logger.info("CreatureAIHandler v3.0 (NPC-only) initialized")

    @staticmethod
    def _text_hash(text: str) -> str:
        """Короткий хэш текста для кэша."""
        return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]

    # ═══════════════════════════════════════════════════════════════
    # ГЛАВНЫЙ ОБРАБОТЧИК: входящий запрос из игры
    # ═══════════════════════════════════════════════════════════════

    def _on_chat_request(self, request: dict):
        """
        Вызывается когда DB Bridge находит новый запрос из игры.

        Теперь ТОЛЬКО для NPC (не ботов). Вся тактика убрана.
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

        # ВАРИАНТ А: работаем ТОЛЬКО с NPC
        if is_player:
            logger.debug("Ignoring bot request (Variant A: NPC-only)")
            return

        logger.info("NPC Dialog: %s '%s' -> %s (entry=%d, guid=%d)",
                   player_name, message[:50], npc_name, npc_entry, npc_guid)

        self._handle_npc_dialog(request, message, channel)

    # ═══════════════════════════════════════════════════════════════
    # ДИАЛОГ С NPC (единственный путь в Варианте А)
    # ═══════════════════════════════════════════════════════════════

    def _handle_npc_dialog(self, request: dict, message: str, channel: str) -> None:
        """
        Обычный диалог с NPC — с памятью, репутацией, квестами.
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
                logger.debug("Cache hit for player %d", player_guid)
                self._send_response(player_guid, npc_guid, npc_entry, cached, False)
                return

        # Обновить/создать данные NPC в WorldState
        self._ensure_entity_exists(npc_guid, npc_name, npc_entry)

        # Получить контекст мира + данные NPC
        ctx = self.world.get_full_context(str(npc_guid))
        entity_data = ctx.get("npc", {})

        # Получить данные игрока (репутация, память)
        player_data = self._get_player_data(player_guid, player_name, npc_guid, npc_entry)

        # Получить профиль NPC (по entry ID — жёсткая персонализация)
        npc_profile = self._get_npc_profile(npc_entry, npc_name, entity_data)

        # Собрать промпт для LLM
        system_prompt = prompts.build_npc_system_prompt(
            npc_profile=npc_profile,
            world_context=ctx,
            player_data=player_data,
            channel=channel
        )
        user_prompt = prompts.build_npc_user_prompt(message, player_data, npc_profile)

        # Отправить в LLM (priority=2 — мезо, диалоги)
        future = self.llm.submit(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.75,
            max_tokens=150,
            priority=2,
        )

        # Обработать результат в отдельном потоке
        threading.Thread(
            target=self._process_llm_response,
            args=(future, req_id, player_guid, npc_guid, npc_entry, 
                  player_name, message, channel),
            daemon=True,
        ).start()

    def _process_llm_response(self, future, req_id, player_guid, npc_guid, 
                             npc_entry, player_name, message, channel):
        """Обработать ответ LLM и отправить в игру."""
        try:
            result = future.result(timeout=15)
        except Exception as e:
            logger.error("LLM request failed for NPC %d: %s", npc_guid, e)
            result = validators._fallback_response("NPC")

        validated = validators.validate_response(result, "NPC")

        # Обновить состояние NPC и игрока
        self._update_entity_state(npc_guid, validated, message, player_guid, player_name, npc_entry)
        self._send_response(player_guid, npc_guid, npc_entry, validated, False)

        # Кэш
        text_hash = self._text_hash(message)
        cache_key = (player_guid, text_hash)
        self._last_talk[player_guid] = time.time()
        self._cache[cache_key] = validated

        # Событие
        self.bus.publish("npc_talk_ended", {
            "npc_guid": npc_guid,
            "npc_entry": npc_entry,
            "player_name": player_name,
            "player_guid": player_guid,
            "player_input": message,
            "channel": channel,
            "response": validated,
        })

        logger.info("NPC %d (entry=%d) responded: '%s'", 
                   npc_guid, npc_entry, validated["speech"][:50])

    # ═══════════════════════════════════════════════════════════════
    # ДАННЫЕ ИГРОКА (репутация, память, квесты)
    # ═══════════════════════════════════════════════════════════════

    def _get_player_data(self, player_guid: int, player_name: str, 
                         npc_guid: int, npc_entry: int) -> dict:
        """
        Собрать данные игрока для контекста диалога.

        Включает: репутацию с этим NPC, историю диалогов,
        активные квесты, общую репутацию.
        """
        # Репутация с конкретным NPC (из MySQL)
        rep_data = self.db.get_reputation(npc_guid, player_guid)
        reputation = rep_data.get("reputation", 0)

        # История диалогов из MySQL (последние 5)
        memory = self.db.get_memory(npc_guid, player_guid, 5)

        # Активные квесты этого игрока (из MySQL)
        active_quests = {}
        try:
            conn = self.db._get_conn()
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT pqp.quest_id, nq.quest_name, pqp.status, pqp.item_count
                    FROM player_quest_progress pqp
                    JOIN npc_quests nq ON pqp.quest_id = nq.quest_id
                    WHERE pqp.player_guid = %s AND pqp.status = 'active'
                """, (player_guid,))
                for row in cur.fetchall():
                    active_quests[row[0]] = {
                        "name": row[1],
                        "status": row[2],
                        "item_count": row[3],
                    }
        except Exception as e:
            logger.error("Failed to get active quests: %s", e)
        finally:
            if 'conn' in locals() and conn is not None:
                conn.close()

        # Квесты, доступные от этого NPC
        available_quests = []
        try:
            conn = self.db._get_conn()
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT quest_id, quest_name FROM npc_quests
                    WHERE giver_npc_entry = %s
                """, (npc_entry,))
                for row in cur.fetchall():
                    # Проверить, не взят ли уже
                    if row[0] not in active_quests:
                        available_quests.append({"id": row[0], "name": row[1]})
        except Exception as e:
            logger.error("Failed to get available quests: %s", e)
        finally:
            if 'conn' in locals() and conn is not None:
                conn.close()

        return {
            "name": player_name,
            "guid": player_guid,
            "reputation": reputation,
            "reputation_text": self._rep_to_text(reputation),
            "memory": memory,
            "active_quests": active_quests,
            "available_quests": available_quests,
            "race": "Unknown",  # TODO: заполнять из game
            "class": "Unknown",
        }

    def _get_npc_profile(self, npc_entry: int, npc_name: str, 
                         entity_data: dict) -> dict:
        """
        Получить профиль NPC — жёсткая персонализация по entry ID.

        Каждый тип NPC (стражник, торговец, квестгивер) имеет
        свой профиль: должность, знания, манера речи.
        """
        # Жёсткие профили по entry ID
        HARDCODED_PROFILES = {
            # Stormwind City Guard (Голдшир, Элвиннский лес) — ТОЧНЫЙ ID
            1423:{
                "name": "Штормградский стражник",
                "role": "Стражник",
                "trait": "Дисциплинированный, бдительный, немного уставший",
                "faction": "Альянс",
                "home_location": "Голдшир, Элвиннский лес",
                "level": 22,
                "knowledge": [
                    "Знает о проблемах с волками в Элвиннском лесу",
                    "Следит за порядком в Голдшире и на дороге",
                    "Знает дорогу до Штормграда и Торгового квартала",
                    "Слышал слухи о бандитах у дороги на Восток",
                    "Помнит нападение гноллов несколько лет назад",
                    "Знает таверну =Забитый кабан= как надёжное место",
                    "Видел, как искатели приключений уходят в шахту к северу",
                ],
                "speech_style": "Официальный, краткий, военный. Иногда позволяет себе сарказм после долгой смены.",
                "mood_default": "нейтральный",
                "can_give_quests": True,
                "quests": ["wolves_goldshire"],
                "gossip_text": "Стойте, путник. Я не со зла, просто выполняю долг.",
            },
            # Stormwind City Guard (старый ID, на всякий случай)
            68: {
                "name": "Штормградский стражник",
                "role": "Стражник",
                "trait": "Дисциплинированный, бдительный",
                "faction": "Альянс",
                "home_location": "Штормград / Элвиннский лес",
                "knowledge": [
                    "Знает о проблемах с волками в лесу",
                    "Следит за порядком",
                ],
                "speech_style": "Официальный, краткий, военный",
                "mood_default": "нейтральный",
                "can_give_quests": True,
                "quests": ["wolves_goldshire"],
            },
            # Stormwind City Guard (альтернативный ID)
            1756: {
                "name": "Штормградский стражник",
                "role": "Стражник",
                "trait": "Дисциплинированный, бдительный",
                "faction": "Альянс",
                "home_location": "Штормград / Элвиннский лес",
                "knowledge": [
                    "Знает о проблемах с волками в лесу",
                    "Следит за порядком",
                ],
                "speech_style": "Официальный, краткий, военный",
                "mood_default": "нейтральный",
                "can_give_quests": True,
                "quests": ["wolves_goldshire"],
            },
        }

        profile = HARDCODED_PROFILES.get(npc_entry)

        if not profile:
            # Fallback: общий профиль для любого стражника
            if "guard" in npc_name.lower() or "страж" in npc_name.lower():
                profile = {
                    "name": npc_name,
                    "role": "Стражник",
                    "trait": "Бдительный, осторожный с незнакомцами",
                    "faction": "Альянс",
                    "home_location": "Неизвестно",
                    "knowledge": [
                        "Следит за порядком в своём районе",
                        "Знает местных жителей",
                    ],
                    "speech_style": "Официальный, краткий",
                    "mood_default": "нейтральный",
                    "can_give_quests": False,
                    "quests": [],
                }
            else:
                # Универсальный fallback
                profile = {
                    "name": npc_name,
                    "role": entity_data.get("role", "Житель"),
                    "trait": entity_data.get("trait", "Обычный"),
                    "faction": entity_data.get("faction", "Нейтральная"),
                    "home_location": "Неизвестно",
                    "knowledge": [],
                    "speech_style": "Обычный",
                    "mood_default": "нейтральный",
                    "can_give_quests": False,
                    "quests": [],
                }

        # Добавляем динамические данные из WorldState
        profile["mood_current"] = entity_data.get("mood", profile["mood_default"])
        profile["dialogue_count"] = entity_data.get("dialogue_count", 0)

        return profile

    # ═══════════════════════════════════════════════════════════════
    # ОТПРАВКА ОТВЕТА В ИГРУ
    # ═══════════════════════════════════════════════════════════════

    def _send_response(self, player_guid: int, npc_guid: int, npc_entry: int, 
                       response: dict, is_player: bool):
        """Записать ответ в ai_responses для Lua."""
        logger.debug("Sending response: player=%d, npc=%d, text='%s...'", 
                    player_guid, npc_guid, response["speech"][:30])

        self.db.write_response(
            player_guid=player_guid,
            npc_guid=npc_guid,
            npc_entry=npc_entry,
            response_text=response["speech"],
            emote_id=response.get("emote_id", 0),
            action_command=response.get("action_command"),
            mood_change=response.get("mood_change", "0"),
        )

    # ═══════════════════════════════════════════════════════════════
    # УПРАВЛЕНИЕ СОСТОЯНИЕМ
    # ═══════════════════════════════════════════════════════════════

    def _ensure_entity_exists(self, guid: int, name: str, entry: int):
        """Создать запись в WorldState если нет."""
        path = f"entities.{guid}"
        existing = self.world.get_nested(path)
        if not existing:
            default_data = {
                "name": name,
                "guid": guid,
                "entry": entry,
                "is_player": False,
                "role": "Житель",
                "trait": "Обычный",
                "mood": "нейтральный",
                "mood_score": 0,
                "faction": "Нейтральная",
                "reputation_to_player": 0,
                "memory": [],
                "dialogue_count": 0,
                "last_channel": "SAY",
            }
            self.world.set_nested(path, default_data)
            logger.debug("Created WorldState for NPC %d (entry=%d)", guid, entry)

    def _update_entity_state(self, guid: int, response: dict, 
                             player_message: str, player_guid: int,
                             player_name: str, npc_entry: int):
        """Обновить состояние NPC после диалога и сохранить в MySQL."""
        path = f"entities.{guid}"

        # Настроение
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

        # Память диалогов в WorldState (последние 10)
        memory = self.world.get_nested(f"{path}.memory", [])
        memory.append({
            "player_guid": player_guid,
            "player_msg": player_message[:100],
            "ai_reply": response.get("speech", "")[:100],
            "timestamp": time.strftime("%H:%M:%S"),
        })
        if len(memory) > 10:
            memory = memory[-10:]
        self.world.set_nested(f"{path}.memory", memory)

        # Сохранить в MySQL (долгосрочная память)
        self.db.save_memory(
            npc_guid=guid,
            npc_entry=npc_entry,
            player_guid=player_guid,
            player_name=player_name,
            memory_type="dialogue",
            content=f"Player: {player_message[:100]} | NPC: {response.get('speech', '')[:100]}",
            player_message=player_message[:100],
            npc_response=response.get("speech", "")[:100],
            mood_after=mood_text,
            reputation_after=self.world.get_nested(f"{path}.reputation_to_player", 0),
        )

        # Обновить репутацию (если mood_change значительный)
        if abs(mood_change) >= 5:
            self.db.update_reputation(
                npc_guid=guid,
                npc_entry=npc_entry,
                player_guid=player_guid,
                player_name=player_name,
                delta=mood_change,
                dialogue_increment=True
            )
        else:
            # Просто увеличить счётчик диалогов
            self.db.update_reputation(
                npc_guid=guid,
                npc_entry=npc_entry,
                player_guid=player_guid,
                player_name=player_name,
                delta=0,
                dialogue_increment=True
            )

        # Счётчик диалогов
        count = self.world.get_nested(f"{path}.dialogue_count", 0)
        self.world.set_nested(f"{path}.dialogue_count", count + 1)

        # Глобальная хронология
        self.world.append_chronology(
            f"{self.world.get_nested('meta.world_hour', 12)}:00 — "
            f"NPC {guid} talked with player {player_guid}"
        )

    @staticmethod
    def _rep_to_text(rep: int) -> str:
        """Числовая репутация в текст."""
        if rep < -50:
            return "враждебный"
        elif rep < 0:
            return "недружелюбный"
        elif rep < 50:
            return "нейтральный"
        elif rep < 100:
            return "дружелюбный"
        else:
            return "почтённый"

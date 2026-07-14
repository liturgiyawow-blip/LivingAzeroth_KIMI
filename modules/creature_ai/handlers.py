"""
CreatureAIHandler v4.0 — обработчик диалогов с NPC

Три уровня профилей:
1. HARDCODED_PROFILES (ручные, крутые)
2. npc_profiles из базы (сгенерированные ранее)
3. Умный fallback из игровых данных + ленивая генерация LLM
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
from wow_connector.game_data import GameDataProvider
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
    def __init__(self, world_state: WorldState, llm_queue: PriorityLLMQueue,
                 event_bus: EventBus, db_bridge: WoWDBBridge):
        self.world = world_state
        self.llm = llm_queue
        self.bus = event_bus
        self.db = db_bridge
        self.game_data = GameDataProvider()

        # Кэш
        self._last_talk: Dict[int, float] = {}
        self._cache: Dict[Tuple[int, str], dict] = {}
        self._cache_ttl = 3.0

        # Загрузчик ролей
        if PERSONA_SYSTEM_AVAILABLE:
            self.persona_loader = get_persona_loader()
            logger.info("Persona system loaded. Active: %s",
                       self.persona_loader.get_current_persona_name())
        else:
            self.persona_loader = None

        # Подписаться на события
        self.db.register_callback(self._on_chat_request)

        logger.info("CreatureAIHandler v4.0 initialized")

    @staticmethod
    def _text_hash(text: str) -> str:
        return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]

    # ═══════════════════════════════════════════════════════════════
    # ГЛАВНЫЙ ОБРАБОТЧИК
    # ═══════════════════════════════════════════════════════════════

    def _on_chat_request(self, request: dict):
        req_id = request.get("id", 0)
        npc_guid = request["npc_guid"]
        npc_entry = request.get("npc_entry", 0)
        npc_name = request.get("npc_name", "Unknown")
        player_name = request["player_name"]
        player_guid = request.get("player_guid", 0)
        message = request["message"]
        channel = request.get("channel_type", "SAY")
        is_player = request.get("target_is_player", False)

        # Только NPC (не боты)
        if is_player:
            logger.debug("Ignoring bot request")
            return

        logger.info("NPC Dialog: %s '%s' -> %s (entry=%d, guid=%d)",
                   player_name, message[:50], npc_name, npc_entry, npc_guid)

        self._handle_npc_dialog(request, message, channel)

    # ═══════════════════════════════════════════════════════════════
    # ДИАЛОГ С NPC
    # ═══════════════════════════════════════════════════════════════

    def _handle_npc_dialog(self, request: dict, message: str, channel: str) -> None:
        req_id = request.get("id", 0)
        npc_guid = request["npc_guid"]
        npc_entry = request.get("npc_entry", 0)
        npc_name = request.get("npc_name", "Unknown")
        player_name = request["player_name"]
        player_guid = request.get("player_guid", 0)

        # Rate limit
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

        # Получить профиль NPC (3 уровня)
        npc_profile = self._get_npc_profile(npc_entry, npc_guid, npc_name)

        # Получить данные игрока
        player_data = self._get_player_data(player_guid, player_name, npc_guid, npc_entry)

        # Получить контекст мира
        ctx = self.world.get_full_context(str(npc_guid))

        # Собрать промпт для LLM
        system_prompt = prompts.build_npc_system_prompt(
            npc_profile=npc_profile,
            world_context=ctx,
            player_data=player_data,
            channel=channel
        )
        user_prompt = prompts.build_npc_user_prompt(message, player_data, npc_profile)

        # Отправить в LLM
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
                  player_name, message, channel, npc_profile),
            daemon=True,
        ).start()

    def _process_llm_response(self, future, req_id, player_guid, npc_guid,
                             npc_entry, player_name, message, channel, npc_profile):
        try:
            result = future.result(timeout=15)
        except Exception as e:
            logger.error("LLM request failed for NPC %d: %s", npc_guid, e)
            result = validators._fallback_response("NPC")

        validated = validators.validate_response(result, "NPC")

        # Обновить состояние
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

        # Если профиль был fallback — запустить ленивую генерацию в фоне
        if npc_profile.get("generated_by") == "fallback_smart":
            self._lazy_generate_profile(npc_entry, npc_guid, npc_name)

    # ═══════════════════════════════════════════════════════════════
    # ТРИ УРОВНЯ ПРОФИЛЕЙ
    # ═══════════════════════════════════════════════════════════════

    def _get_npc_profile(self, npc_entry: int, npc_guid: int, npc_name: str) -> dict:
        """
        Получить профиль NPC по 3 уровням:
        1. HARDCODED_PROFILES (ручные)
        2. npc_profiles из базы (сгенерированные)
        3. Умный fallback из игровых данных
        """
        # Уровень 1: Хардкод
        profile = self._get_hardcoded_profile(npc_entry)
        if profile:
            logger.debug("Profile L1 (hardcoded) for entry=%d", npc_entry)
            return profile

        # Уровень 2: База
        profile = self.db.get_npc_profile(npc_entry, npc_guid)
        if profile:
            logger.debug("Profile L2 (database) for entry=%d, guid=%d", npc_entry, npc_guid)
            return profile

        # Уровень 3: Умный fallback
        logger.info("Profile L3 (fallback) for entry=%d, guid=%d — generating smart fallback", npc_entry, npc_guid)
        profile = self.game_data.build_smart_fallback(npc_entry, npc_guid, npc_name)

        # Сохранить fallback в базу (чтобы не генерировать каждый раз)
        self.db.save_npc_profile(
            npc_entry=npc_entry,
            npc_guid=npc_guid,
            npc_name=profile["name"],
            profile=profile,
            generated_by="fallback"
        )

        return profile

    def _get_hardcoded_profile(self, npc_entry: int) -> Optional[dict]:
        """Уровень 1: Ручные профили."""
        HARDCODED_PROFILES = {
            1423: {
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
        if profile:
            profile["generated_by"] = "human"
        return profile

    # ═══════════════════════════════════════════════════════════════
    # ЛЕНИВАЯ ГЕНЕРАЦИЯ ПРОФИЛЯ (фоновая, через LLM)
    # ═══════════════════════════════════════════════════════════════

    def _lazy_generate_profile(self, npc_entry: int, npc_guid: int, npc_name: str):
        """
        Фоновая генерация профиля через LLM.
        Вызывается после первого диалога с fallback-профилем.
        """
        logger.info("Lazy profile generation started: entry=%d, guid=%d", npc_entry, npc_guid)

        # Получить fallback-данные для контекста
        fallback = self.game_data.build_smart_fallback(npc_entry, npc_guid, npc_name)

        # Промпт для LLM
        system_prompt = """Ты — генератор профилей NPC для World of Warcraft.

Создай яркий, запоминающийся профиль NPC на основе базовых данных.
Ответь ТОЛЬКО в формате JSON:

{
  "name": "имя",
  "role": "роль",
  "trait": "черты характера",
  "faction": "фракция",
  "home_location": "место",
  "knowledge": ["факт 1", "факт 2", "факт 3", "факт 4", "факт 5"],
  "speech_style": "стиль речи",
  "mood_default": "нейтральный",
  "can_give_quests": false,
  "quests": [],
  "gossip_text": "приветственная фраза"
}

Правила:
- Знания: 5-7 конкретных фактов о мире (места, события, люди)
- Стиль речи: опиши как говорит (словарный запас, темп, манера)
- Имя: если есть имя — используй, иначе придумай подходящее
- НЕ выдумывай глобальные события, которых нет в лоре WoW WotLK"""

        user_prompt = f"""Создай профиль для NPC:

Имя в базе: {fallback['name']}
Роль: {fallback['role']}
Локация: {fallback['home_location']}
Фракция: {fallback['faction']}
Тип: {fallback.get('creature_type', 'humanoid')}

Базовые знания:
{chr(10).join('- ' + k for k in fallback['knowledge'])}

Сгенерируй профиль."""

        # Отправить в LLM с низким приоритетом (фоновая задача)
        future = self.llm.submit(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.8,
            max_tokens=400,
            priority=3,  # макро — низкий приоритет
        )

        # Обработать в фоне
        def _on_profile_generated(fut, entry, guid):
            try:
                result = fut.result(timeout=60)
                parsed = self._parse_profile_from_llm(result)

                if parsed:
                    # Сохранить в базу как LLM-сгенерированный
                    self.db.save_npc_profile(
                        npc_entry=entry,
                        npc_guid=guid,
                        npc_name=parsed.get("name", npc_name),
                        profile=parsed,
                        generated_by="llm",
                        generation_prompt=user_prompt
                    )
                    logger.info("Profile generated by LLM: entry=%d, guid=%d", entry, guid)
                else:
                    logger.warning("Failed to parse LLM profile for entry=%d", entry)

            except Exception as e:
                logger.error("Profile generation failed: %s", e)

        threading.Thread(
            target=_on_profile_generated,
            args=(future, npc_entry, npc_guid),
            daemon=True,
        ).start()

    def _parse_profile_from_llm(self, result: dict) -> Optional[dict]:
        """Распарсить JSON-профиль из ответа LLM."""
        if not isinstance(result, dict):
            return None

        # Если LLM вернул уже dict (llm_queue распарсил)
        if "role" in result and "knowledge" in result:
            return result

        # Иначе ищем JSON в тексте
        text = result.get("speech", "") or str(result)
        try:
            # Ищем JSON в тексте
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                data = json.loads(text[start:end+1])
                if "role" in data and "knowledge" in data:
                    return data
        except json.JSONDecodeError:
            pass

        return None

    # ═══════════════════════════════════════════════════════════════
    # ДАННЫЕ ИГРОКА
    # ═══════════════════════════════════════════════════════════════

    def _get_player_data(self, player_guid: int, player_name: str,
                         npc_guid: int, npc_entry: int) -> dict:
        rep_data = self.db.get_reputation(npc_guid, player_guid)
        reputation = rep_data.get("reputation", 0)
        memory = self.db.get_memory(npc_guid, player_guid, 5)

        active_quests = {}
        try:
            conn = self.db._get_ai_conn()
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT pqp.quest_id, nq.quest_name, pqp.status, pqp.item_count
                    FROM player_quest_progress pqp
                    JOIN npc_quests nq ON pqp.quest_id = nq.quest_id
                    WHERE pqp.player_guid = %s AND pqp.status = 'active'
                """, (player_guid,))
                for row in cur.fetchall():
                    active_quests[row[0]] = {
                        "name": row[1], "status": row[2], "item_count": row[3],
                    }
        except Exception as e:
            logger.error("Failed to get active quests: %s", e)
        finally:
            if 'conn' in locals() and conn is not None:
                conn.close()

        available_quests = []
        try:
            conn = self.db._get_ai_conn()
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT quest_id, quest_name FROM npc_quests
                    WHERE giver_npc_entry = %s
                """, (npc_entry,))
                for row in cur.fetchall():
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
            "race": "Unknown",
            "class": "Unknown",
        }

    # ═══════════════════════════════════════════════════════════════
    # ОТПРАВКА ОТВЕТА
    # ═══════════════════════════════════════════════════════════════

    def _send_response(self, player_guid: int, npc_guid: int, npc_entry: int,
                       response: dict, is_player: bool):
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
            "player_guid": player_guid,
            "player_msg": player_message[:100],
            "ai_reply": response.get("speech", "")[:100],
            "timestamp": time.strftime("%H:%M:%S"),
        })
        if len(memory) > 10:
            memory = memory[-10:]
        self.world.set_nested(f"{path}.memory", memory)

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
            self.db.update_reputation(
                npc_guid=guid,
                npc_entry=npc_entry,
                player_guid=player_guid,
                player_name=player_name,
                delta=0,
                dialogue_increment=True
            )

        count = self.world.get_nested(f"{path}.dialogue_count", 0)
        self.world.set_nested(f"{path}.dialogue_count", count + 1)

        self.world.append_chronology(
            f"{self.world.get_nested('meta.world_hour', 12)}:00 — "
            f"NPC {guid} talked with player {player_guid}"
        )

    @staticmethod
    def _rep_to_text(rep: int) -> str:
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
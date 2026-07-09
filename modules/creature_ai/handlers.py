"""
CreatureAIHandler — обработчик диалогов с NPC
"""

import time
import threading
import logging
from typing import Dict

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
        
        self._last_talk: Dict[str, float] = {}  # npc_guid -> timestamp
        self._cache: Dict[str, dict] = {}        # npc_guid -> response
        self._cache_ttl = 3.0  # секунды
        
        # Подписаться на события от DB Bridge
        self.db.register_callback(self._on_chat_request)
        
        logger.info("CreatureAIHandler initialized")
    
    def _on_chat_request(self, request: dict):
        """Вызывается когда DB Bridge находит новый запрос из игры"""
        npc_guid = request["npc_guid"]
        npc_entry = request["npc_entry"]
        npc_name = request.get("npc_name", "NPC")
        player_name = request["player_name"]
        message = request["message"]
        zone = request.get("zone", "Unknown")
        
        # Rate limit
        now = time.time()
        if npc_guid in self._last_talk:
            if now - self._last_talk[npc_guid] < 3.0:
                cached = self._cache.get(str(npc_guid))
                if cached:
                    self._send_response(npc_entry, npc_guid, cached)
                    return
        
        # Обновить/создать данные NPC в WorldState
        self._ensure_npc_exists(npc_guid, npc_name, npc_entry)
        
        # Получить контекст
        ctx = self.world.get_full_context(str(npc_guid))
        npc_data = ctx.get("npc", {})
        
        # Данные игрока (заглушка, можно расширить)
        player_data = {
            "name": player_name,
            "race": "Human",
            "class": "Warrior",
            "reputation": npc_data.get("reputation_to_player", 0),
        }
        
        # Промпты
        system_prompt = prompts.build_system_prompt(npc_data, ctx, player_data)
        user_prompt = prompts.build_user_prompt(message, zone)
        
        # Отправить в LLM с приоритетом 1 (Микро — диалог)
        future = self.llm.submit(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.75,
            max_tokens=120,
            priority=1,
        )
        
        # Обработать результат в отдельном потоке
        threading.Thread(
            target=self._process_llm_response,
            args=(future, npc_entry, npc_guid, player_name, message),
            daemon=True,
        ).start()
    
    def _process_llm_response(self, future, npc_entry, npc_guid, player_name, message):
        try:
            result = future.result(timeout=15)
        except Exception as e:
            logger.error("LLM request failed for NPC %d: %s", npc_guid, e)
            result = validators._fallback_response("NPC")
        
        # Валидация
        validated = validators.validate_response(result, "NPC")
        
        # Обновить WorldState
        self._update_npc_state(npc_guid, validated, message)
        
        # Записать в БД (Eluna Lua прочитает и заставит NPC говорить)
        self._send_response(npc_entry, npc_guid, validated)
        
        # Кэш
        self._last_talk[str(npc_guid)] = time.time()
        self._cache[str(npc_guid)] = validated.copy()
        
        # Опубликовать событие
        self.bus.publish("npc_talk_ended", {
            "npc_guid": npc_guid,
            "player_name": player_name,
            "player_input": message,
            "response": validated,
        })
        
        logger.info("NPC %d responded: '%s'", npc_guid, validated["speech"][:50])
    
    def _send_response(self, npc_entry: int, npc_guid: int, response: dict):
        logger.info("=== _send_response called ===")
        logger.info("npc_entry=%d, npc_guid=%d", npc_entry, npc_guid)
        logger.info("response=%s", response)
        self.db.write_response(
            npc_entry=npc_entry,
            npc_guid=npc_guid,
            response_text=response["speech"],
            emote_id=response.get("emote_id", 0),
        )
    
    def _ensure_npc_exists(self, npc_guid: int, npc_name: str, npc_entry: int):
        """Создать запись NPC в WorldState если нет"""
        path = f"creatures.{npc_guid}"
        existing = self.world.get_nested(path)
        if not existing:
            self.world.set_nested(path, {
                "name": npc_name,
                "entry": npc_entry,
                "guid": npc_guid,
                "role": "Житель",
                "trait": "Обычный",
                "mood": "нейтральный",
                "faction": "Stormwind",
                "reputation_to_player": 0,
                "memory": [],
                "dialogue_count": 0,
            })
    
    def _update_npc_state(self, npc_guid: int, response: dict, player_message: str):
        """Обновить состояние NPC после диалога"""
        path = f"creatures.{npc_guid}"
        
        # Изменить настроение
        mood_change = int(response.get("mood_change", 0))
        current_mood = self.world.get_nested(f"{path}.mood_score", 0)
        new_mood = max(-100, min(100, current_mood + mood_change))
        self.world.set_nested(f"{path}.mood_score", new_mood)
        
        # Запомнить диалог
        memory = self.world.get_nested(f"{path}.memory", [])
        memory.append(f"Игрок сказал: {player_message[:100]}")
        if len(memory) > 10:
            memory = memory[-10:]
        self.world.set_nested(f"{path}.memory", memory)
        
        # Счётчик
        count = self.world.get_nested(f"{path}.dialogue_count", 0)
        self.world.set_nested(f"{path}.dialogue_count", count + 1)
        
        # Хронология мира
        self.world.append_chronology(
            f"{self.world.get_nested('meta.world_hour')}:00 — "
            f"NPC {npc_guid} поговорил с игроком"
        )
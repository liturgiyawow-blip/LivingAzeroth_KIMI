"""
combat_handlers.py — Обработчик post-combat фраз

Получает данные боя из ai_requests (channel_type='POST-COMBAT'),
анализирует, отправляет в LLM, записывает ответ в ai_responses.
"""

import json
import logging
import random
import threading
from typing import Dict, List, Optional

import config
from core.llm_queue import PriorityLLMQueue
from core.world_state import WorldState
from wow_connector.db_bridge import WoWDBBridge

from modules.combat_ai import combat_prompts

logger = logging.getLogger(__name__)


class CombatAnalyst:
    """
    Анализирует бой и генерирует post-combat фразы от ботов.
    
    Работает параллельно с CreatureAIHandler — тот же механизм
    ai_requests/ai_responses, но отдельная логика обработки.
    """
    
    def __init__(self, world_state: WorldState, llm_queue: PriorityLLMQueue,
                 db_bridge: WoWDBBridge):
        self.world = world_state
        self.llm = llm_queue
        self.db = db_bridge
        
        # Регистрируем себя как обработчик POST-COMBAT
        self.db.register_callback(self._on_combat_data)
        
        logger.info("CombatAnalyst initialized")
    
    def _on_combat_data(self, request: dict):
        """Обработка данных боя из ai_requests."""
        if request.get("channel_type") != "POST-COMBAT":
            return  # Не наш запрос — пусть CreatureAIHandler разбирается
        
        try:
            data = json.loads(request["message"])
        except json.JSONDecodeError as e:
            logger.error("Failed to parse combat data: %s", e)
            return
        
        speaker_guid = data.get("speaker_guid", 0)
        speaker_name = data.get("speaker_name", "Unknown")
        
        logger.info("Post-combat phrase triggered! Speaker: %s (guid=%d), Severity: %d",
                   speaker_name, speaker_guid, data.get("severity", 0))
        
        self._generate_phrase(data, request)
    
    def _generate_phrase(self, data: dict, request: dict):
        """Сгенерировать фразу через LLM."""
        
        speaker_guid = data["speaker_guid"]
        speaker_name = data["speaker_name"]
        
        # Собираем контекст для промпта
        context = self._build_combat_context(data)
        
        # Строим промпты
        system_prompt = combat_prompts.build_combat_system_prompt(context)
        user_prompt = combat_prompts.build_combat_user_prompt()
        
        # Отправляем в LLM (приоритет 2 — важно, но не срочно)
        future = self.llm.submit(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.8,
            max_tokens=120,
            priority=2,
        )
        
        # Обработка ответа в фоновом потоке
        threading.Thread(
            target=self._process_llm_response,
            args=(future, data, request),
            daemon=True,
        ).start()
    
    def _process_llm_response(self, future, data: dict, request: dict):
        """Обработать ответ LLM и записать в ai_responses."""
        
        speaker_guid = data["speaker_guid"]
        speaker_name = data["speaker_name"]
        leader_guid = data.get("leader_guid", speaker_guid)
        
        try:
            result = future.result(timeout=30)
        except Exception as e:
            logger.error("LLM failed for combat phrase (speaker=%s): %s", speaker_name, e)
            # Fallback — случайная фраза из примеров
            result = self._fallback_phrase(data)
        
        # Валидируем
        speech = result.get("speech", "") if isinstance(result, dict) else str(result)
        emote_id = 0
        if isinstance(result, dict):
            try:
                emote_id = int(result.get("emote_id", 0))
            except (ValueError, TypeError):
                emote_id = 0
        
        # Обрезаем до 255 символов (лимит WoW Say)
        if len(speech) > 255:
            speech = speech[:252] + "..."
        
        # Выбираем эмоцию по контексту если LLM не дал
        if emote_id == 0:
            emote_id = self._choose_emote(data)
        
        # Записываем в ai_responses
        # player_guid = leader группы (кто получит ответ в Lua)
        # npc_guid = speaker (бот, который "говорит")
        self.db.write_response(
            player_guid=leader_guid,
            npc_guid=speaker_guid,
            npc_entry=0,
            response_text=speech,
            emote_id=emote_id,
            action_command=None,
            mood_change="0",
        )
        
        logger.info("Post-combat phrase from %s: '%s...' (emote=%d)",
                   speaker_name, speech[:50], emote_id)
        
        # Обновляем мировую хронику
        self.world.append_chronology(
            f"{self.world.get_nested('meta.world_hour', 12)}:00 — "
            f"Bot {speaker_name} commented on combat"
        )
    
    def _build_combat_context(self, data: dict) -> dict:
        """Преобразовать raw данные Lua в читаемый контекст."""
        
        # Добавляем список всех участников из данных
        participants = []
        for p in data.get("participants", []):
            if isinstance(p, dict):
                participants.append(p.get("name", "Unknown"))
            else:
                participants.append(str(p))
        
        # Если participants пуст — берём из casualties, wounded, heroes
        if not participants:
            seen = set()
            for name in data.get("casualties", []):
                seen.add(name)
            for w in data.get("wounded", []):
                if isinstance(w, dict):
                    seen.add(w.get("name", ""))
            for name in data.get("heroes", []):
                seen.add(name)
            participants = list(seen)
        
        return {
            "speaker_name": data.get("speaker_name", "Unknown"),
            "speaker_race": data.get("speaker_race", "Unknown"),
            "speaker_class": data.get("speaker_class", "Unknown"),
            "duration_desc": data.get("duration_desc", "краткая схватка"),
            "duration_sec": data.get("duration_sec", 0),
            "severity": data.get("severity", 0),
            "modifiers": data.get("modifiers", []),
            "casualties": data.get("casualties", []),
            "wounded": data.get("wounded", []),
            "heroes": data.get("heroes", []),
            "boss_name": data.get("boss_name"),
            "enemy_count": data.get("enemy_count", 0),
            "participants": participants,
        }
    
    def _choose_emote(self, data: dict) -> int:
        """Выбрать эмоцию по контексту боя."""
        casualties = data.get("casualties", [])
        wounded = data.get("wounded", [])
        severity = data.get("severity", 0)
        
        if casualties:
            return 18  # cry — скорбь о павших
        elif severity >= 50:
            return 66  # bow — почтение перед тяжёлой битвой
        elif wounded:
            return 25  # point — указывает на раненого
        elif severity >= 30:
            return 1   # talk — серьёзный разговор
        elif severity <= 5:
            return 3   # wave — лёгкость, облегчение
        return 1  # talk — по умолчанию
    
    def _fallback_phrase(self, data: dict) -> dict:
        """Fallback если LLM недоступен — случайная фраза из примеров."""
        severity = data.get("severity", 0)
        casualties = data.get("casualties", [])
        wounded = data.get("wounded", [])
        heroes = data.get("heroes", [])
        boss_name = data.get("boss_name")
        
        examples = combat_prompts.EXAMPLE_PHRASES
        
        if casualties:
            pool = examples.get("death_comrade", ["Прощай, брат..."])
        elif heroes:
            pool = examples.get("critical_ally", ["Ты выстоял до конца!"])
        elif wounded:
            pool = examples.get("wounded_ally", ["Ты в порядке, друг?"])
        elif boss_name:
            pool = examples.get("boss_kill", ["Враг пал!"])
        elif severity >= 15:
            pool = examples.get("long_fight", ["Долгий бой..."])
        else:
            pool = examples.get("easy_no_wounds", ["Легко было."])
        
        speech = random.choice(pool)
        
        # Подставляем имена если есть
        if casualties and "{name}" in speech:
            speech = speech.replace("{name}", casualties[0])
        if boss_name and "{boss}" in speech:
            speech = speech.replace("{boss}", boss_name)
        
        return {
            "speech": speech,
            "emote_id": self._choose_emote(data),
        }
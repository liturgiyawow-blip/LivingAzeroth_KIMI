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
from wow_connector.game_data import GameDataProvider

from modules.combat_ai import combat_prompts
from modules.creature_ai.memory_engine import BotMemoryEngine, MemoryConfig

logger = logging.getLogger(__name__)
prompt_logger = logging.getLogger("llm_prompts")


class CombatAnalyst:
    """
    Анализирует бой и генерирует post-combat фразы от ботов.
    """
    
    def __init__(self, world_state: WorldState, llm_queue: PriorityLLMQueue,
                 db_bridge: WoWDBBridge):
        self.world = world_state
        self.llm = llm_queue
        self.db = db_bridge
        
        self.db.register_callback(self._on_combat_data)
        
        # ═══════════════════════════════════════════════════════════════
        # НОВОЕ: движок памяти для post-combat контекста
        # ═══════════════════════════════════════════════════════════════
        self.memory = BotMemoryEngine(MemoryConfig(
            max_context_chars=400,
            l2_episodes_limit=2,
            l3_facts_limit=1,
            l4_social_limit=1,
        ))
        self.game_data = GameDataProvider()
        
        logger.info("CombatAnalyst initialized")

    def _on_combat_data(self, request: dict):
        """Обработка данных боя из ai_requests."""
        if request.get("channel_type") != "POST-COMBAT":
            return
        
        try:
            data = json.loads(request["message"])
        except json.JSONDecodeError as e:
            logger.error("Failed to parse combat data: %s", e)
            return
        
        speaker_guid = data.get("speaker_guid", 0)
        speaker_name = data.get("speaker_name", "Unknown")
        leader_guid = data.get("leader_guid", speaker_guid)
        
        logger.info("Post-combat phrase triggered! Speaker: %s (guid=%d), Leader: %s (guid=%d), Severity: %d",
                   speaker_name, speaker_guid, data.get("leader_name", "Unknown"), leader_guid, data.get("severity", 0))
        
        self._generate_phrase(data, request)
    
    def _generate_phrase(self, data: dict, request: dict):
        """Сгенерировать фразу через LLM."""
        
        speaker_guid = data["speaker_guid"]
        speaker_name = data["speaker_name"]
        leader_guid = data.get("leader_guid", speaker_guid)
        
        # ═══════════════════════════════════════════════════════════════
        # НОВОЕ: достаём память спикера для контекста боя
        # ═══════════════════════════════════════════════════════════════
        search_query = " ".join(filter(None, [
            data.get("boss_name", ""),
            " ".join(data.get("enemies_names", [])),
            "бой" if data.get("casualties") else "",
            "падение товарищей" if data.get("casualties") else "победа",
        ]))

        active_memory = ""
        try:
            active_memory = self.memory.retrieve(
                bot_guid=speaker_guid,
                player_message=search_query,
                player_name=data.get("leader_name", ""),
                player_guid=leader_guid,
                bot_race=data.get("speaker_race", ""),
                bot_class=data.get("speaker_class", ""),
            )
        except Exception as e:
            logger.error("Memory retrieve failed: %s", e)

        context = self._build_combat_context(data)
        
        system_prompt = combat_prompts.build_combat_system_prompt(
            context, memory_context=active_memory
        )
        user_prompt = combat_prompts.build_combat_user_prompt()
        
        # ═══════════════════════════════════════════════════════════════
        # ЛОГИРОВАНИЕ COMBAT-ПРОМПТА
        # ═══════════════════════════════════════════════════════════════
        prompt_logger.debug(
            f"[COMBAT] Speaker={speaker_name} (guid={speaker_guid}) | "
            f"Leader={data.get('leader_name', 'Unknown')} (guid={leader_guid})\n"
            f"Enemies={context.get('enemies_names', [])} | "
            f"Duration={context.get('duration_desc', '?')} ({context.get('duration_sec', 0)}s) | "
            f"Severity={context.get('severity', 0)} | Category={context.get('duration_category', '?')}\n"
            f"--- SYSTEM PROMPT ---\n{system_prompt}\n"
            f"--- USER PROMPT ---\n{user_prompt}\n"
            f"--- END ---"
        )
        
        # FIX v5.3: Увеличили токены для длинных фраз
        future = self.llm.submit(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.9,
            max_tokens=200,
            priority=2,
        )
        
        threading.Thread(
            target=self._process_llm_response,
            args=(future, data, request, leader_guid),
            daemon=True,
        ).start()
    
    def _process_llm_response(self, future, data: dict, request: dict, leader_guid: int):
        """Обработать ответ LLM и записать в ai_responses."""
        
        speaker_guid = data["speaker_guid"]
        speaker_name = data["speaker_name"]
        
        try:
            result = future.result(timeout=30)
        except Exception as e:
            logger.error("LLM failed for combat phrase (speaker=%s): %s", speaker_name, e)
            result = self._fallback_phrase(data)
        
        speech = result.get("speech", "") if isinstance(result, dict) else str(result)
        emote_id = 0
        if isinstance(result, dict):
            try:
                emote_id = int(result.get("emote_id", 0))
            except (ValueError, TypeError):
                emote_id = 0
        
        if len(speech) > 255:
            speech = speech[:252] + "..."
        
        if emote_id == 0:
            emote_id = self._choose_emote(data)
        
        logger.info("Writing response: player_guid=%d (leader), npc_guid=%d (speaker), text='%s...'",
                   leader_guid, speaker_guid, speech[:50])

        # ═══════════════════════════════════════════════════════════════
        # ЛОГИРОВАНИЕ COMBAT-ОТВЕТА
        # ═══════════════════════════════════════════════════════════════
        raw_content = result.get("speech", str(result)) if isinstance(result, dict) else str(result)
        prompt_logger.debug(
            f"[COMBAT] Speaker={speaker_name} (guid={speaker_guid}) | RESPONSE\n"
            f"--- RAW LLM OUTPUT ---\n{raw_content[:2000]}\n"
            f"--- VALIDATED ---\n"
            f"speech={speech[:200]}\n"
            f"emote={emote_id}\n"
            f"--- END ---"
        )
        
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
        
        self.world.append_chronology(
            f"{self.world.get_nested('meta.world_hour', 12)}:00 — "
            f"Bot {speaker_name} commented on combat"
        )

        # ═══════════════════════════════════════════════════════════════
        # НОВОЕ: записываем эпизод боя в память спикера + социалку с лидером
        # ═══════════════════════════════════════════════════════════════
        try:
            casualties = data.get("casualties", [])
            wounded = data.get("wounded", [])
            enemies = data.get("enemies_names", [])
            boss = data.get("boss_name")
            duration = data.get("duration_sec", 0)
            zone_id = data.get("zone_id", 0)
            area_id = data.get("area_id", 0)

            # Резолв локации
            location_str = "неизвестно"
            subzone_str = None
            if zone_id:
                zn = self.game_data.get_area_name(zone_id)
                if zn:
                    location_str = zn
            if area_id and area_id != zone_id:
                an = self.game_data.get_area_name(area_id)
                if an and an != location_str:
                    subzone_str = an

            is_boss = bool(boss)
            enemy_str = boss if boss else (enemies[0] if enemies else "враги")

            # Эмоция и интенсивность
            if casualties:
                emotion = "скорбь"
                intensity = 85
                is_trauma = True
            elif wounded:
                emotion = "напряжение"
                intensity = 60
                is_trauma = False
            elif is_boss:
                emotion = "гордость"
                intensity = 75
                is_trauma = False
            elif duration > 120:
                emotion = "усталость"
                intensity = 50
                is_trauma = False
            else:
                emotion = "нейтральный"
                intensity = 30
                is_trauma = False

            summary = f"Бой с {enemy_str}. "
            if casualties:
                summary += f"Пали: {', '.join(casualties)}. "
            elif duration > 60:
                summary += "Долгая схватка, едва выстояли. "
            else:
                summary += "Разобрались быстро. "

            self.memory.record_episode(
                bot_guid=speaker_guid,
                episode_type="battle",
                title=f"Бой против {enemy_str}",
                summary=summary.strip(),
                location=location_str,
                subzone=subzone_str,
                involved=(casualties + [data.get("leader_name", "Лидер")]) if casualties else [data.get("leader_name", "Лидер")],
                emotional_tag=emotion,
                intensity=intensity,
                is_trauma=is_trauma,
                is_boss=is_boss,
            )

            # Социальная память о лидере
            if leader_guid and leader_guid != speaker_guid:
                trust_delta = 5 if not casualties else (-10 if data.get("leader_name") in casualties else 3)
                self.memory.update_social(
                    bot_guid=speaker_guid,
                    target_guid=leader_guid,
                    target_name=data.get("leader_name", "Лидер"),
                    target_type="player",
                    trust_delta=trust_delta,
                    shared_note=f"Бой в {location_str} против {enemy_str}. {'Победа' if not casualties else 'Есть потери'}.",
                )

            # Чистим старый трэш, но боссов оставляем
            self.memory.cleanup_episodes(speaker_guid)

        except Exception as e:
            logger.error("Failed to record combat episode: %s", e)
    
    def _build_combat_context(self, data: dict) -> dict:
        """Преобразовать raw данные Lua в читаемый контекст."""
        
        participants = []
        for p in data.get("participants", []):
            if isinstance(p, dict):
                participants.append(p.get("name", "Unknown"))
            else:
                participants.append(str(p))
        
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
            "triggers": data.get("triggers", {}),
            "casualties": data.get("casualties", []),
            "wounded": data.get("wounded", []),
            "heroes": data.get("heroes", []),
            "boss_name": data.get("boss_name"),
            "enemy_count": data.get("enemy_count", 0),
            "participants": participants,
            "enemies_names": data.get("enemies_names", []),
            "speaker_main_hand": data.get("speaker_main_hand", "руки"),
            "leader_name": data.get("leader_name", "Unknown"),
            "leader_main_hand": data.get("leader_main_hand", "руки"),
            # НОВОЕ: локация из Lua
            "zone_id": data.get("zone_id", 0),
            "area_id": data.get("area_id", 0),
        }
    
    def _choose_emote(self, data: dict) -> int:
        """Выбрать эмоцию по контексту боя."""
        casualties = data.get("casualties", [])
        wounded = data.get("wounded", [])
        severity = data.get("severity", 0)
        triggers = data.get("triggers", {})
        
        if casualties:
            return 18  # cry
        elif "solo_survivor" in triggers:
            return 18  # cry
        elif severity >= 50:
            return 66  # bow
        elif wounded:
            return 25  # point
        elif severity >= 30:
            return 1   # talk
        elif severity <= 5:
            return 3   # wave
        return 1
    
    def _fallback_phrase(self, data: dict) -> dict:
        """Fallback если LLM недоступен."""
        severity = data.get("severity", 0)
        casualties = data.get("casualties", [])
        wounded = data.get("wounded", [])
        heroes = data.get("heroes", [])
        boss_name = data.get("boss_name")
        triggers = data.get("triggers", {})
        
        examples = combat_prompts.EXAMPLE_PHRASES
        
        if "solo_survivor" in triggers:
            pool = examples.get("solo_survivor", ["Я... один..."])
        elif casualties:
            pool = examples.get("death_comrade", ["Прощай, брат..."])
        elif heroes:
            pool = examples.get("critical_ally", ["Ты выстоял до конца!"])
        elif wounded:
            pool = examples.get("wounded_ally", ["Ты в порядке, друг?"])
        elif boss_name:
            pool = examples.get("boss_kill", ["Враг пал!"])
        elif "long_fight" in triggers:
            pool = examples.get("long_fight", ["Долгий бой..."])
        elif severity >= 15:
            pool = examples.get("long_fight", ["Долгий бой..."])
        else:
            pool = examples.get("easy_no_wounds", ["Легко было."])
        
        speech = random.choice(pool)
        
        if casualties and "{name}" in speech:
            speech = speech.replace("{name}", casualties[0])
        if boss_name and "{boss}" in speech:
            speech = speech.replace("{boss}", boss_name)
        
        return {
            "speech": speech,
            "emote_id": self._choose_emote(data),
        }
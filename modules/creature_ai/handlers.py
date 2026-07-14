"""
CreatureAIHandler v4.1 — обработчик диалогов с NPC и ботами

Упрощённая версия: только LLM-диалоги, rich профили ботов.
"""

import time
import json
import hashlib
import threading
import logging
from typing import Dict, Tuple

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
    """
    Главный обработчик диалогов с NPC и ботами.
    """

    def __init__(self, world_state: WorldState, llm_queue: PriorityLLMQueue,
                 event_bus: EventBus, db_bridge: WoWDBBridge):
        self.world = world_state
        self.llm = llm_queue
        self.bus = event_bus
        self.db = db_bridge
        
        self._last_talk: Dict[int, float] = {}
        self._cache: Dict[Tuple[int, str], dict] = {}
        self._cache_ttl = 3.0
        
        self.db.register_callback(self._on_chat_request)
        
        logger.info("CreatureAIHandler v4.1 initialized")

    @staticmethod
    def _text_hash(text: str) -> str:
        return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]

    def _on_chat_request(self, request: dict):
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
        
        self._handle_chat_dialog(request, message, is_player, channel)

    def _handle_chat_dialog(self, request: dict, message: str, 
                            is_player: bool, channel: str) -> None:
        npc_guid = request["npc_guid"]
        npc_entry = request.get("npc_entry", 0)
        npc_name = request.get("npc_name", "Unknown")
        player_name = request["player_name"]
        player_guid = request.get("player_guid", 0)
        
        now = time.time()
        text_hash = self._text_hash(message)
        cache_key = (player_guid, text_hash)

        if now - self._last_talk.get(player_guid, 0) < self._cache_ttl:
            cached = self._cache.get(cache_key)
            if cached:
                logger.debug("Cache hit for player %d", player_guid)
                self._send_response(player_guid, npc_guid, npc_entry, cached, is_player)
                return
        
        self._ensure_entity_exists(npc_guid, npc_name, npc_entry, is_player)
        
        ctx = self.world.get_full_context(str(npc_guid))
        entity_data = ctx.get("npc", {})
        
        player_data = {
            "name": player_name,
            "guid": player_guid,
            "race": "Unknown",
            "class": "Unknown",
            "reputation": entity_data.get("reputation_to_player", 0),
        }
        
        if is_player:
            system_prompt = prompts.build_bot_system_prompt(entity_data, ctx, player_data, channel)
        else:
            system_prompt = prompts.build_system_prompt(entity_data, ctx, player_data)
        
        user_prompt = prompts.build_user_prompt(message, channel, is_player)
        
        priority = 1 if channel in ("PARTY", "WHISPER", "SAY-BOT") else 2
        
        future = self.llm.submit(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.75,
            max_tokens=150,
            priority=priority,
        )

        threading.Thread(
            target=self._process_llm_response,
            args=(future, player_guid, npc_guid, npc_entry, 
                  player_name, message, is_player, channel),
            daemon=True,
        ).start()
    
    def _process_llm_response(self, future, player_guid, npc_guid, 
                             npc_entry, player_name, message, is_player, channel):
        try:
            result = future.result(timeout=15)
        except Exception as e:
            logger.error("LLM request failed for %s (guid=%d): %s", 
                        "bot" if is_player else "NPC", npc_guid, e)
            result = validators._fallback_response("bot" if is_player else "NPC")
        
        validated = validators.validate_response(result, "bot" if is_player else "NPC")
        self._update_entity_state(npc_guid, validated, message, is_player, player_guid)
        self._send_response(player_guid, npc_guid, npc_entry, validated, is_player)
        
        text_hash = self._text_hash(message)
        cache_key = (player_guid, text_hash)
        self._last_talk[player_guid] = time.time()
        self._cache[cache_key] = validated

        self.bus.publish("npc_talk_ended", {
            "npc_guid": npc_guid,
            "npc_entry": npc_entry,
            "player_name": player_name,
            "player_guid": player_guid,
            "player_input": message,
            "channel": channel,
            "response": validated,
        })
        
        logger.info("%s %d responded in %s: '%s'", 
                   "Bot" if is_player else "NPC", npc_guid, channel, validated["speech"][:50])
    
    def _send_response(self, player_guid: int, npc_guid: int, npc_entry: int, 
                       response: dict, is_player: bool):
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
    
    # ═══════════════════════════════════════════════════════════════
    # RICH ПРОФИЛИ БОТОВ
    # ═══════════════════════════════════════════════════════════════
    
    def _ensure_entity_exists(self, guid: int, name: str, entry: int, is_player: bool):
        """Создать запись в WorldState если нет."""
        path = f"entities.{guid}"
        existing = self.world.get_nested(path)
        if not existing:
            if is_player:
                char_info = self.db.get_character_info(guid)
                if char_info:
                    race = char_info.get("race", "Unknown")
                    class_name = char_info.get("class", "Unknown")
                    level = char_info.get("level", 1)
                    
                    role = self._get_role_by_class(class_name)
                    trait = self._get_trait_by_race_class(race, class_name)
                    speech_style = self._get_speech_style(race, class_name)
                    faction = self._get_faction_by_race(race)
                    
                    default_data = {
                        "name": name,
                        "guid": guid,
                        "entry": entry,
                        "is_player": True,
                        "race": race,
                        "class": class_name,
                        "level": level,
                        "role": role,
                        "trait": trait,
                        "mood": "нейтральный",
                        "mood_score": 0,
                        "faction": faction,
                        "reputation_to_player": 0,
                        "memory": [],
                        "dialogue_count": 0,
                        "last_channel": "SAY",
                        "speech_style": speech_style,
                    }
                    self.world.set_nested(path, default_data)
                    logger.info("Created rich profile for bot %s (%s %s, level %d)", 
                               name, race, class_name, level)
                    return
            
            # Fallback для NPC или если character info недоступен
            default_data = {
                "name": name,
                "guid": guid,
                "entry": entry,
                "is_player": is_player,
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
            logger.debug("Created default WorldState for %s %d", 
                        "bot" if is_player else "NPC", guid)
    
    def _get_role_by_class(self, class_name: str) -> str:
        roles = {
            "Warrior": "Воин", "Paladin": "Паладин", "Hunter": "Охотник",
            "Rogue": "Разбойник", "Priest": "Жрец", "Death Knight": "Рыцарь Смерти",
            "Shaman": "Шаман", "Mage": "Маг", "Warlock": "Чернокнижник", "Druid": "Друид"
        }
        return roles.get(class_name, "Авантюрист")

    def _get_trait_by_race_class(self, race: str, class_name: str) -> str:
        traits = {
            ("Dwarf", "Warrior"): "Крепкий дварф-воин, любит эль и боевые песни",
            ("Orc", "Warrior"): "Суровый орк-воин, чтит честь Орды",
            ("Human", "Paladin"): "Благородный паладин, служитель Света",
            ("Night Elf", "Druid"): "Мудрый друид, хранитель природы",
            ("Undead", "Warlock"): "Мрачный чернокнижник, владеющий тенями",
            ("Dwarf", "Paladin"): "Дварф-паладин, крепкий как скала, верный Свету",
            ("Gnome", "Mage"): "Гном-маг, изобретательный и немного чокнутый",
            ("Troll", "Shaman"): "Тролль-шаман, говорит с духами предков",
        }
        return traits.get((race, class_name), f"{race} {class_name}")

    def _get_speech_style(self, race: str, class_name: str) -> str:
        race_styles = {
            "Dwarf": "Грубоватый, с акцентом, любит поговорить о пиве и битвах. Использует 'ладно', 'приятель', 'крепкий'.",
            "Orc": "Прямой, грубый, военный тон. Часто упоминает честь и Орду.",
            "Human": "Обычный, дружелюбный, адаптивный.",
            "Night Elf": "Мелодичный, мудрый, любит природные метафоры.",
            "Undead": "Хриплый, циничный, с чёрным юмором.",
            "Tauren": "Спокойный, размеренный, уважительный.",
            "Gnome": "Быстрый, оживлённый, любит технические термины.",
            "Troll": "Экзотический акцент, часто упоминает духов и лоа.",
            "Blood Elf": "Горделивый, изысканный, иногда высокомерный.",
            "Draenei": "Спокойный, духовный, с лёгким акцентом.",
        }
        base = race_styles.get(race, "Обычный")
        
        class_addon = {
            "Warrior": " Говорит коротко, по-военному. Любит сравнивать бой с пивом.",
            "Paladin": " Упоминает Свет, защиту, честь. Величественный тон.",
            "Hunter": " Говорит о тропах, зверях, выживании. Практичный.",
            "Rogue": " Тихий, хитрый, любит намёки. Не доверяет сразу.",
            "Priest": " Успокаивающий, духовный. Может процитировать молитву.",
            "Death Knight": " Холодный, мрачный, немногословный. Помнит смерть.",
            "Shaman": " Мистический, говорит о духах, стихиях, предках.",
            "Mage": " Учёный, любит магические термины. Иногда высокомерен.",
            "Warlock": " Тёмный, шепчущий, опасный. Любит тайны.",
            "Druid": " Природный, гармоничный. Говорит о балансе.",
        }
        return base + class_addon.get(class_name, "")

    def _get_faction_by_race(self, race: str) -> str:
        alliance = {"Human", "Dwarf", "Night Elf", "Gnome", "Draenei"}
        horde = {"Orc", "Undead", "Tauren", "Troll", "Blood Elf"}
        if race in alliance:
            return "Альянс"
        elif race in horde:
            return "Орда"
        return "Нейтральная"

    def _update_entity_state(self, guid: int, response: dict, player_message: str, 
                             is_player: bool, player_guid: int):
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
        
        # FIX: Добавляем memory с player_guid для контекста
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
        
        count = self.world.get_nested(f"{path}.dialogue_count", 0)
        self.world.set_nested(f"{path}.dialogue_count", count + 1)
        
        self.world.append_chronology(
            f"{self.world.get_nested('meta.world_hour', 12)}:00 — "
            f"{'Bot' if is_player else 'NPC'} {guid} talked with player"
        )
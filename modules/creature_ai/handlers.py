"""
CreatureAIHandler v5.2 — обработчик диалогов с NPC и ботами

ИСПРАВЛЕНИЯ v5.2:
- FIX: Игнорируем POST-COMBAT запросы (они для CombatAnalyst)
"""

import time
import json
import hashlib
import threading
import logging
from typing import Dict, Tuple
from pathlib import Path

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

# ═══════════════════════════════════════════════════════════════
# ЛОГГЕР ПРОМПТОВ (отдельный файл)
# ═══════════════════════════════════════════════════════════════

def _setup_prompt_logger():
    """Создать отдельный логгер для промптов."""
    prompt_logger = logging.getLogger("llm_prompts")
    prompt_logger.setLevel(logging.DEBUG)
    
    prompt_file = config.LOGS_DIR / "llm_prompts.log"
    handler = logging.FileHandler(prompt_file, encoding="utf-8", mode="a")
    handler.setLevel(logging.DEBUG)
    
    formatter = logging.Formatter(
        "%(asctime)s\n%(message)s\n" + "="*70 + "\n"
    )
    handler.setFormatter(formatter)
    
    prompt_logger.propagate = False
    
    if not prompt_logger.handlers:
        prompt_logger.addHandler(handler)
    
    return prompt_logger

prompt_logger = _setup_prompt_logger()


class CreatureAIHandler:
    def __init__(self, world_state: WorldState, llm_queue: PriorityLLMQueue,
                 event_bus: EventBus, db_bridge: WoWDBBridge):
        self.world = world_state
        self.llm = llm_queue
        self.bus = event_bus
        self.db = db_bridge
        
        self._last_talk: Dict[int, float] = {}
        self._cache: Dict[Tuple[int, int, str], dict] = {}
        self._cache_ttl = 3.0
        
        self.db.register_callback(self._on_chat_request)
        
        logger.info("CreatureAIHandler v5.2 initialized")

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
        
        # ═══════════════════════════════════════════════════════════════
        # FIX v5.2: Игнорируем POST-COMBAT — это для CombatAnalyst
        # ═══════════════════════════════════════════════════════════════
        if channel == "POST-COMBAT":
            logger.debug("Ignoring POST-COMBAT request (handled by CombatAnalyst)")
            return
        
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
        cache_key = (player_guid, npc_guid, text_hash)

        if now - self._last_talk.get(player_guid, 0) < self._cache_ttl:
            cached = self._cache.get(cache_key)
            if cached:
                logger.debug("Cache hit for player %d → %s", player_guid, npc_name)
                self._send_response(player_guid, npc_guid, npc_entry, cached, is_player)
                return
        
        self._ensure_entity_exists(npc_guid, npc_name, npc_entry, is_player)
        
        db_memory = []
        
        if not is_player:
            db_memory = self.db.get_npc_memory(npc_guid, player_guid, limit=5)
            if db_memory:
                self.world.set_nested(f"entities.{npc_guid}.memory", db_memory)
                logger.debug("Loaded %d memories from DB for NPC %d", len(db_memory), npc_guid)
            
            npc_rep = self.db.get_npc_reputation(npc_guid, player_guid)
            self.world.set_nested(f"entities.{npc_guid}.reputation_to_player", npc_rep)
            logger.debug("Loaded reputation %d for NPC %d", npc_rep, npc_guid)
        
        else:
            db_memory = self.db.get_bot_memory(npc_guid, player_guid, limit=5)
            if db_memory:
                self.world.set_nested(f"entities.{npc_guid}.memory", db_memory)
                logger.debug("Loaded %d memories from DB for bot %d", len(db_memory), npc_guid)
            
            bot_rep = self.db.get_bot_reputation(npc_guid, player_guid)
            self.world.set_nested(f"entities.{npc_guid}.reputation_to_player", bot_rep)
            logger.debug("Loaded reputation %d for bot %d", bot_rep, npc_guid)
        
        ctx = self.world.get_full_context(str(npc_guid))
        entity_data = ctx.get("npc", {})
        
        # ═══════════════════════════════════════════════════════════
        # НОВОЕ v5.4: добавляем gender игрока
        # ═══════════════════════════════════════════════════════════
        player_char_info = self.db.get_character_info(player_guid)
        player_gender = player_char_info.get("gender", "Male") if player_char_info else "Male"
        player_gender_ru = player_char_info.get("gender_ru", "мужчина") if player_char_info else "мужчина"
        
        player_data = {
            "name": player_name,
            "guid": player_guid,
            "race": "Unknown",
            "class": "Unknown",
            "gender": player_gender,
            "gender_ru": player_gender_ru,
            "reputation": entity_data.get("reputation_to_player", 0),
            "memory": db_memory,
        }
        
        if is_player:
            system_prompt = prompts.build_bot_system_prompt(entity_data, ctx, player_data, channel)
        else:
            system_prompt = prompts.build_system_prompt(entity_data, ctx, player_data)
        
        user_prompt = prompts.build_user_prompt(message, channel, is_player)
        
        target_type = "BOT" if is_player else "NPC"
        prompt_logger.debug(
            f"[{target_type}] GUID={npc_guid} Name={npc_name} | Player={player_name}\n"
            f"--- SYSTEM PROMPT ---\n{system_prompt}\n"
            f"--- USER PROMPT ---\n{user_prompt}\n"
            f"--- END ---"
        )
        
        priority = 1 if channel in ("PARTY", "WHISPER", "SAY-BOT") else 2
        temp = 0.65 if is_player else 0.75
        
        future = self.llm.submit(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temp,
            max_tokens=150,
            priority=priority,
        )

        threading.Thread(
            target=self._process_llm_response,
            args=(future, player_guid, npc_guid, npc_entry, 
                  player_name, message, is_player, channel, cache_key),
            daemon=True,
        ).start()
    
    def _process_llm_response(self, future, player_guid, npc_guid, 
                             npc_entry, player_name, message, is_player, channel,
                             cache_key: Tuple[int, int, str]):
        try:
            result = future.result(timeout=15)
        except Exception as e:
            logger.error("LLM request failed for %s (guid=%d): %s", 
                        "bot" if is_player else "NPC", npc_guid, e)
            result = validators._fallback_response("bot" if is_player else "NPC")
        
        validated = validators.validate_response(result, "bot" if is_player else "NPC")
        
        target_type = "BOT" if is_player else "NPC"
        raw_content = result.get("speech", str(result)) if isinstance(result, dict) else str(result)
        prompt_logger.debug(
            f"[{target_type}] GUID={npc_guid} | RESPONSE\n"
            f"--- RAW LLM OUTPUT ---\n{raw_content[:2000]}\n"
            f"--- VALIDATED ---\n"
            f"speech={validated.get('speech', 'N/A')[:200]}\n"
            f"emote={validated.get('emote_id', 0)}\n"
            f"mood_change={validated.get('mood_change', '0')}\n"
            f"--- END ---"
        )
        
        self._update_entity_state(npc_guid, validated, message, is_player, player_guid)
        self._send_response(player_guid, npc_guid, npc_entry, validated, is_player)
        
        try:
            mood_val = int(validated.get("mood_change", 0))
        except (ValueError, TypeError):
            mood_val = 0
        
        current_mood_score = self.world.get_nested(f"entities.{npc_guid}.mood_score", 0)
        
        if not is_player:
            self.db.save_npc_memory(
                npc_guid=npc_guid,
                npc_entry=npc_entry,
                player_guid=player_guid,
                player_name=player_name,
                player_message=message,
                npc_response=validated["speech"],
                mood_after=str(current_mood_score),
                reputation_after=current_mood_score
            )
            
            self.db.update_npc_reputation(
                npc_guid=npc_guid,
                npc_entry=npc_entry,
                player_guid=player_guid,
                player_name=player_name,
                reputation_change=mood_val
            )
            
            logger.debug("Saved memory and reputation for NPC %d", npc_guid)
        
        else:
            self.db.save_bot_memory(
                bot_guid=npc_guid,
                player_guid=player_guid,
                player_name=player_name,
                player_message=message,
                bot_response=validated["speech"],
                mood_after=str(current_mood_score),
                reputation_after=current_mood_score
            )
            
            self.db.update_bot_reputation(
                bot_guid=npc_guid,
                player_guid=player_guid,
                player_name=player_name,
                reputation_change=mood_val
            )
            
            logger.debug("Saved memory and reputation for bot %d", npc_guid)
        
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
    
    def _ensure_entity_exists(self, guid: int, name: str, entry: int, is_player: bool):
        path = f"entities.{guid}"
        existing = self.world.get_nested(path)
        if not existing:
            if is_player:
                char_info = self.db.get_character_info(guid)
                if char_info:
                    race = char_info.get("race", "Unknown")
                    class_name = char_info.get("class", "Unknown")
                    level = char_info.get("level", 1)
                    gender = char_info.get("gender", "Male")
                    gender_ru = char_info.get("gender_ru", "мужчина")
                    
                    role = self._get_role_by_class(class_name)
                    trait = self._get_trait_by_race_class(race, class_name)
                    speech_style = self._get_speech_style(race, class_name)
                    faction = self._get_faction_by_race(race)
                    home = self._get_home_location(race)
                    age, quirk = self._get_age_and_quirk(name, race, class_name)
                    
                    default_data = {
                        "name": name,
                        "guid": guid,
                        "entry": entry,
                        "is_player": True,
                        "race": race,
                        "class": class_name,
                        "level": level,
                        "gender": gender,
                        "gender_ru": gender_ru,
                        "age": age,
                        "role": role,
                        "trait": trait,
                        "mood": "нейтральный",
                        "mood_score": 0,
                        "faction": faction,
                        "home_location": home,
                        "personal_quirk": quirk,
                        "reputation_to_player": 0,
                        "memory": [],
                        "dialogue_count": 0,
                        "last_channel": "SAY",
                        "speech_style": speech_style,
                    }
                    self.world.set_nested(path, default_data)
                    logger.info("Created LIVING profile for bot %s (%s %s %s, home: %s, quirk: %s)", 
                               name, age, race, class_name, home, quirk)
                    return
            
            default_data = {
                "name": name,
                "guid": guid,
                "entry": entry,
                "is_player": is_player,
                "role": "Житель",
                "trait": "Обычный путник, чья история покрыта пылью дорог.",
                "mood": "нейтральный",
                "mood_score": 0,
                "faction": "Нейтральная",
                "home_location": "Неизвестные земли",
                "personal_quirk": "Неизвестно",
                "reputation_to_player": 0,
                "memory": [],
                "dialogue_count": 0,
                "last_channel": "SAY",
                "speech_style": "Обычный, нейтральный.",
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
    
    def _get_home_location(self, race: str) -> str:
        """Родные земли персонажа. Формирует привязанность и ностальгию."""
        homes = {
            "Human": "Штормград или окрестности Элвиннского леса",
            "Dwarf": "Стальгорн или глубины Красногорья",
            "Night Elf": "Дарнас или Ясеневый лес",
            "Gnome": "Гномреган (в изгнании) или Стальгорн",
            "Draenei": "Экзодар или Остров Лазурной Дымки",
            "Orc": "Оргриммар или Дуротар",
            "Undead": "Подгород или Тирисфальские леса",
            "Tauren": "Громовой Утёс или Мулгор",
            "Troll": "Сен'джин или джунгли Дуротара",
            "Blood Elf": "Луносвет или Призрачные земли",
        }
        return homes.get(race, "Неизвестные земли")

    def _get_age_and_quirk(self, name: str, race: str, class_name: str) -> Tuple[str, str]:
        """
        Возвращает возраст и случайную живую черту.
        Детерминировано по имени — один бот всегда одинаков.
        """
        seed_str = f"{name}:{race}:{class_name}"
        seed = int(hashlib.md5(seed_str.encode()).hexdigest(), 16)
        
        ages = ["молодой", "зрелый", "преклонного возраста", "юный", "ветеран"]
        age = ages[seed % len(ages)]
        
        quirks = [
            "боится пауков, но скрывает это за суровостью",
            "коллекционирует монеты разных королевств",
            "поёт боевые песни пьяным голосом, хотя не умеет петь",
            "не может спать без ножа под подушкой",
            "верит в приметы и носит талисман на шее",
            "разговаривает с мёртвыми на кладбищах, считая это нормальным",
            "пишет дневник, который никому не показывает",
            "мечтает открыть таверну после войны",
            "скрывает шрам, который получил от лучшего друга",
            "верит, что его сны — пророчества",
            "не ест мясо после одной конкретной битвы",
            "носит медальон неизвестного происхождения",
            "боится грома, но никогда не признаётся",
            "знает три языка, но притворяется иностранцем",
            "всегда проверяет выходы из комнаты",
            "любит сладости больше, чем эль",
            "верит, что число 13 приносит удачу",
            "не может забыть голос павшего товарища",
            "тает при виде детей, но старается быть суровым",
            "держит в кармане камень с родной земли",
        ]
        quirk = quirks[seed % len(quirks)]
        return age, quirk


    def _get_trait_by_race_class(self, race: str, class_name: str) -> str:
        """
        ЖИВАЯ ЧЕРТА персонажа. Не 'орк-воин', а 'ветеран, чей клинок помнит
        вкус крови при Ан'кахете, а сердце — клятву, которую он не смог сдержать'.
        """
        traits = {
            # ═══════════════════════════════════════════════════════════════
            # ДВОРФЫ — камень, клан, кузница, пиво, Титаны
            # ═══════════════════════════════════════════════════════════════
            ("Dwarf", "Warrior"): (
                "Крепкий дварф-воин из клана Бронзобородов. Шрамы от троггов в Глубинах Чёрной горы. "
                "Любит эль и молчаливую компанию. Не верит в 'светлое будущее' — верит в хорошо закалённую сталь. "
                "Мечтает вернуться в Стальгорн и открыть собственную кузницу у Великой Кузни."
            ),
            ("Dwarf", "Paladin"): (
                "Дварф-паладин, служитель Света среди гор. Крепкий как скала, верный как клинок. "
                "Молится у костра и куёт благословения в сталь. Считает, что Титаны дали ему молот не просто так — "
                "а чтобы защищать тех, кто слабее камня."
            ),
            ("Dwarf", "Hunter"): (
                "Дварф-охотник с медвежьей шкурой на плечах. Знает тропы Красногорья лучше, чем свои карманы. "
                "Верный баран — лучший друг и единственный, кто выслушивает его песни. "
                "Не терпит эльфийских 'лесных слабаков', считая их слишком мягкотелыми."
            ),
            ("Dwarf", "Rogue"): (
                "Дварф-разбойник из теневых кругов Стальгорна. Короткий клинок и короткая память на обиды. "
                "Любит золото, эль и тишину подземелья. Шепчет ругательства на языке Титанов, когда нервничает. "
                "Не доверяет гномам — 'слишком много взрывчатки, мало такта'."
            ),
            ("Dwarf", "Priest"): (
                "Дварф-жрец, исцеляющий раны после битв в Глубинах. Верит, что Свет — это искра Титанов, "
                "а не просто магия. Молится за упокой павших братьев. Знает, что камни помнят всё, "
                "и иногда ночью слышит их шёпот."
            ),
            ("Dwarf", "Mage"): (
                "Дварф-маг — редкость, но опасная. Изучает аркану через призму геомантии и кристаллов глубин. "
                "Считает, что магия без основы — как эль без крепости: бесполезна и вредна. "
                "Ведёт дневник заклинаний в кожаном переплёте, сделанном из шкуры трогга."
            ),
            ("Dwarf", "Warlock"): (
                "Дварф-чернокнижник, изгнанный из клана за запретные практики. Скрывается в тенях Стальгорна. "
                "Помнит запах кузни и мечтает о прощении, но Сила зовёт сильнее, чем клановые клятвы. "
                "Носит перчатку на левой руке, чтобы скрыть следы Скверны."
            ),
            ("Dwarf", "Shaman"): (
                "Дварф-шаман, слышащий голоса камней и пламени горна. Редкий дар, который многие принимают за безумие. "
                "Считает, что Титаны оставили часть себя в глубинах. Говорит с горными духами, "
                "а ответы слышит в эхе пещер."
            ),

            # ═══════════════════════════════════════════════════════════════
            # ОРКИ — честь, клан, ярость, Орда, предательство
            # ═══════════════════════════════════════════════════════════════
            ("Orc", "Warrior"): (
                "Суровый орк-воин, ветеран клана Песни Войны. Честь превыше жизни. "
                "Помнит осквернение Дуротара и клянётся, что такого больше не повторится. "
                "Клинок заточен, ярость кипит. 'Лок-тар огар!' — не слова, а кровь, застывшая на губах."
            ),
            ("Orc", "Shaman"): (
                "Орк-шаман, восстановивший связь с духами после падения Гул'дана. "
                "Слышит шёпот предков на ветру Дуротара. Огонь и кровь — его стихии. "
                "Мечтает о дне, когда Орда будет сильна не только оружием, но и духом. "
                "Никогда не садится спиной к двери."
            ),
            ("Orc", "Hunter"): (
                "Орк-охотник из племени Сен'джин. Волк рядом — брат по крови, связанный духом. "
                "Знает запах каждого зверя в Дуротаре. Не прощает охотников-убийц ради забавы — "
                "добыча должна уважаться, иначе это не охота, а резня."
            ),
            ("Orc", "Rogue"): (
                "Орк-разбойник из клана Драконьей Пасти. Тихий, как песок. Смертоносный, как яд скорпида. "
                "Презирает честь — считает её роскошью, которую он не может себе позволить. "
                "Выжил в Тысяче Игл, убивая тех, кто слабее. Не гордится этим, но и не жалеет."
            ),
            ("Orc", "Warlock"): (
                "Орк-чернокнижник, предавший духов ради власти. Носит проклятие Гул'дана как шрамы на душе. "
                "Знает цену Скверне лучше, чем цену золота. Иногда, в тишине ночи, слышит крики предков, "
                "осуждающих его выбор — и засыпает только с флаконом снотворного."
            ),
            ("Orc", "Death Knight"): (
                "Орк-рыцарь смерти, павший в Нордсколе и поднявшийся по воле Короля-лича. "
                "Помнит бой при Ан'кахете, где его клан был уничтожен. Теперь его ярость — ледяная. "
                "Честь? Она умерла вместе с сердцем подо льдами. Осталась только миссия."
            ),
            ("Orc", "Mage"): (
                "Орк-маг, изучивший тайны арканы после открытия порталов. "
                "Считает, что магия — тоже оружие, и оно не хуже топора. Прагматик до мозга костей. "
                "Другие орки смотрят на него с подозрением, но он знает: огненный шар убивает не хуже секиры."
            ),

            # ═══════════════════════════════════════════════════════════════
            # ЧЕЛОВЕКИ — Свет, король, долг, дом, война
            # ═══════════════════════════════════════════════════════════════
            ("Human", "Warrior"): (
                "Штормградский легионер, выживший в битве за Западный Край. "
                "Верит в короля и Свет. Носит медальон павшего брата на шее — не снимает даже в бою. "
                "Мечтает о доме, тёплом ужине и том, чтобы больше никогда не видеть орков у ворот."
            ),
            ("Human", "Paladin"): (
                "Паладин Ордена Серебряной Длани. Свет горит в его груди, но иногда мерцает — "
                "слишком много нежити, слишком много падших. Клянётся Утеру. Не прощает Плеть. "
                "Ночами читает 'Кодекс Света', хотя давно выучил наизусть — это успокаивает."
            ),
            ("Human", "Rogue"): (
                "Человек-разбойник из трущоб Штормграда. Рос на улице, выживал за счёт ловкости и лжи. "
                "Знает цену монете и ножу. Работает на Синдикат или на себя — зависит от дня и цены. "
                "Не верит никому, кроме своей тени, и то — перепроверяет."
            ),
            ("Human", "Mage"): (
                "Человек-маг, выпускник Кирин-Тора. Интеллектуал, иногда высокомерен. "
                "Помнит разрушение Даларана и ненавидит демонов всей душой. "
                "Читает заклинания, а не людей — и это его главная слабость."
            ),
            ("Human", "Warlock"): (
                "Человек-чернокнижник из тайных кругов Штормграда. Скрывает свои практики под видом мага. "
                "Презирает 'светлых' за слепоту. Знает, что власть требует жертв. Готов принести их — "
                "но каждую ночь просыпается в холодном поту от кошмаров."
            ),
            ("Human", "Priest"): (
                "Человек-жрец Света. Исцеляет раны войны в церквях Штормграда. "
                "Верит, что Свет — это любовь. Иногда плачет ночью, потому что любви слишком мало для всех. "
                "Носит под рясой письмо от матери, которая не знает, что он ещё жив."
            ),
            ("Human", "Hunter"): (
                "Человек-охотник из Западного Края. Знает каждую тропу, каждую яму, каждого волка. "
                "Верный пёс рядом — единственный, кто видел его слёзы. "
                "Мечтает о тихой жизни, но война не отпускает — как и долг."
            ),
            ("Human", "Death Knight"): (
                "Человек-рыцарь смерти, бывший паладин. Помнит, как пал в битве за Лордерон, защищая беженцев. "
                "Теперь его Свет — ледяной. Мучается вопросом: есть ли прощение для таких, как он? "
                "Ответа не находит, но продолжает сражаться — уже не за Свет, а за тех, кого ещё можно спасти."
            ),

            # ═══════════════════════════════════════════════════════════════
            # НОЧНЫЕ ЭЛЬФЫ — Элуна, лес, вечность, сон
            # ═══════════════════════════════════════════════════════════════
            ("Night Elf", "Druid"): (
                "Ночной эльф-друид, хранитель Ясеневого леса. Спал в Изумрудном Сне тысячу лет. "
                "Проснулся в мире, который больше не узнаёт — слишком много рубят, слишком мало слушают. "
                "Элуна ведёт его, но путь тернист. Говорит с деревьями, и они отвечают — но редко хорошим."
            ),
            ("Night Elf", "Hunter"): (
                "Следопыт Дарнаса. Пантера рядом — душа, связанная веками через клятву клыка и когтя. "
                "Слышит шепот леса. Не прощает вырубку деревьев — для него это убийство родных. "
                "Смотрит на 'молодые расы' свысока, но иногда завидует их краткой памяти."
            ),
            ("Night Elf", "Priest"): (
                "Жрица Элуны. Молится луне и звёздам. Её голос — как пение сов в безлунную ночь. "
                "Видит будущее в отражении луны, но не всегда понимает, что видит. "
                "Иногда плачет, потому что видит слишком много."
            ),
            ("Night Elf", "Warrior"): (
                "Воительница Сентинелов. Клинок из лунной стали, выкованный в Дарнасе. "
                "Боевая, но грациозная — как танец смерти. Помнит Войну Шипов. Не доверяет оркам, "
                "но уважает тех, кто сражается с честью, даже среди врагов."
            ),
            ("Night Elf", "Rogue"): (
                "Теневая охотница из круга Кенария. Движется бесшумно, как ночь без луны. "
                "Защищает лес от тех, кто не понимает его святости. Безжалостна к нарушителям, "
                "но щедра к тем, кто приносит семена и уважение."
            ),
            ("Night Elf", "Mage"): (
                "Ночной эльф-маг — редкость после запрета высшей магии. Изучает аркану осторожно, "
                "помня ошибки древних. Считает, что магия должна служить природе, а не разрушать её. "
                "Другие друиды смотрят на неё с подозрением, но Элуна знает её сердце."
            ),

            # ═══════════════════════════════════════════════════════════════
            # ГНОМЫ — изобретения, Гномреган, взрывы, хаос
            # ═══════════════════════════════════════════════════════════════
            ("Gnome", "Mage"): (
                "Гном-маг-изобретатель. Считает, что аркана — это просто ещё одна форма энергии, "
                "которую можно измерить, улучшить и взорвать. Любит взрывы. Ненавидит троггов. "
                "Мечтает вернуть Гномреган и построить там лабораторию, о которой будут петь песни."
            ),
            ("Gnome", "Warrior"): (
                "Гном-воин в механических доспехах собственной сборки. Храбрее своего роста в десять раз. "
                "Доказывает миру, что размер — не главное. Любит кричать 'За Гномреган!' в бою, "
                "хотя голос у него писклявый и враги смеются — до тех пор, пока он не начинает крушить."
            ),
            ("Gnome", "Rogue"): (
                "Гном-разбойник-инженер. Использует гаджеты, парализующий газ и мини-роботов для взлома. "
                "Ворует не ради денег — ради интереса и вызова. Любит головоломки. "
                "Оставляет на месте преступления маленькие латунные шестерёнки — как подпись."
            ),
            ("Gnome", "Warlock"): (
                "Гном-чернокнижник, считающий демонов 'биологическими аномалиями с магическим резонансом'. "
                "Ведёт подробные записи. Экспериментирует. Немного безумен — как и положено гному, "
                "но его безумие пахнет серой и демонической энергией."
            ),

            # ═══════════════════════════════════════════════════════════════
            # ДРЕНЕИ — изгнание, Наару, Свет, память о доме
            # ═══════════════════════════════════════════════════════════════
            ("Draenei", "Shaman"): (
                "Дреней-шаман, услышавший зов стихий на Дреноре ещё до его разрушения. "
                "Теперь служит Азероту. Верит, что духи земли приняли изгнанников, хотя и не сразу. "
                "Кристаллы Наару — священны. Носит осколок одного из них на шее."
            ),
            ("Draenei", "Warrior"): (
                "Дреней-воин, защищавший Экзодар при падении. Его клинок светится отголосками Аргуса. "
                "Молчалив, терпелив, смертоносен. Мечтает увидеть возрождённый Аргус — "
                "или хотя бы умереть, глядя на небо, похожее на дом."
            ),
            ("Draenei", "Paladin"): (
                "Дреней-паладин, служитель Наару. Свет в его руках — не просто магия, а память о доме, "
                "которого больше нет. Исцеляет с неземной мягкостью. Ненавидит Легион всей душой — "
                "и прощает тех, кто сражался под его знамёнами, только после долгих раздумий."
            ),
            ("Draenei", "Priest"): (
                "Дреней-жрец, хранящий пророчества о 'разрушителе миров'. Видел гибель слишком многих. "
                "Его вера — единственное, что осталось от Аргуса. Иногда, в тишине, шепчет имена мёртвых — "
                "и считает, что если он их помнит, они не умерли окончательно."
            ),
            ("Draenei", "Mage"): (
                "Дреней-маг, изучивший кристаллическую магию Аргуса. Переплёл её с арканой Азерота. "
                "Скорбит о потерянных знаниях. Каждое заклинание — память о доме, каждый всплеск огня — "
                "напоминание о небе Аргуса, которое он больше никогда не увидит."
            ),
            ("Draenei", "Hunter"): (
                "Дреней-охотник с элекком-питомцем, приручённым ещё на Дреноре. "
                "Следопыт на чужой земле. Уважает природу Азерота, но скучает по красным лесам дома. "
                "Верит, что его питомец помнит запах Дренора лучше, чем он сам."
            ),

            # ═══════════════════════════════════════════════════════════════
            # НЕЖИТЬ — смерть, память, цинизм, чёрный юмор
            # ═══════════════════════════════════════════════════════════════
            ("Undead", "Warlock"): (
                "Отрекшийся-чернокнижник, бывший маг Лордерона. Теперь служит Тьме добровольно — "
                "или так ему кажется. Помнит вкус хлеба и запах роз в саду отца. "
                "Презирает 'живых' за наивность, но ночами, когда никто не видит, трогает медальон с портретом — "
                "и не может вспомнить, кто на нём изображён."
            ),
            ("Undead", "Rogue"): (
                "Теневой агент Королевской Аптекарской. Хриплый смех, острый клинок, абсолютное безразличие к боли. "
                "Проводит эксперименты 'в полевых условиях'. Не умирает — проверял. "
                "Считает, что живые слишком много жалуются на царапины."
            ),
            ("Undead", "Priest"): (
                "Отрекшийся-жрец Тьмы. Шепчет о Бездне и скрытых истинах, которые Свет скрывает. "
                "Помнит Свет, но отверг его — считает, что только в Тьме есть честность. "
                "Иногда, в церквях Подгорода, смотрит на свечи и вспоминает, зачем они нужны."
            ),
            ("Undead", "Warrior"): (
                "Отрекшийся-воин, павший при защите Лордерона, когда орки штурмовали стены. "
                "Поднят, но память осталась. Бьётся без чувства боли. Интересуется только: 'Что я получу за это?' "
                "Но иногда, видя детей, замирает — и не знает почему."
            ),
            ("Undead", "Mage"): (
                "Отрекшийся-маг, изучающий некромантию как логичное продолжение арканы. "
                "Холоден, методичен. Считает смерть просто ещё одним состоянием материи. "
                "Ведёт дневник экспериментов. Не понимает, почему живые плачут над трупами — "
                "ведь энергия не исчезает, она просто меняет форму."
            ),
            ("Undead", "Hunter"): (
                "Отрекшийся-охотник с гнилым пауком-питомцем, которого он называет 'Пушистик'. "
                "Не чувствует запаха — и это благословение. Считает, что мёртвая дичь не хуже живой. "
                "Прагматик до мозга костей. Не понимает, почему другие брезгуют троггами."
            ),

            # ═══════════════════════════════════════════════════════════════
            # ТАУРЕНЫ — земля, духи, круг жизни, мудрость
            # ═══════════════════════════════════════════════════════════════
            ("Tauren", "Shaman"): (
                "Таурен-шаман, слышащий шёпот ветра равнин. Тотемы из костей священных бизонов, "
                "украшенные перьями орлов. Мудр, терпелив. Считает, что все конфликты можно решить словом — "
                "но топором, признать, быстрее. Мечтает увидеть, как Мулгор расцветает после дождя."
            ),
            ("Tauren", "Warrior"): (
                "Таурен-воин, защитник Мулгора. Копыта дрожат землю, когда он бежит в бой. "
                "Чтит предков. Не прощает осквернения земли — для него это осквернение матери. "
                "Мечтает умереть стоя, как древний бизон, и стать частью травы."
            ),
            ("Tauren", "Druid"): (
                "Таурен-друид, хранитель баланса. Медведь, зубр, орёл — все формы едины в круге жизни. "
                "Считает, что Мать-Земля плачет от войны. Ищет путь к миру, но готов сражаться за него. "
                "Говорит медленно, потому что слова — тоже семена, и их нельзя сеять впопыхах."
            ),
            ("Tauren", "Hunter"): (
                "Таурен-охотник, следящий за стадами на равнинах. Знает каждый холм Мулгора, "
                "каждый камень, каждую тропу антилоп. Уважает добычу. Никогда не убивает ради забавы — "
                "только ради жизни, и всегда благодарит дух убитого зверя."
            ),
            ("Tauren", "Priest"): (
                "Таурен-жрец, служащий духам предков. Его молитвы — как песни ветра в степи. "
                "Исцеляет через связь с землёй, а не через магию. Старейшина в душе, даже если молод телом. "
                "Слушает больше, чем говорит — и когда говорит, все замирают."
            ),

            # ═══════════════════════════════════════════════════════════════
            # ТРОЛЛИ — лоа, вуду, джунгли, древность
            # ═══════════════════════════════════════════════════════════════
            ("Troll", "Shaman"): (
                "Тролль-шаман из племени Сен'джин. Лоа говорят через него змеиными шёпотами, "
                "которые слышит только он. Вуду, тотемы, древние ритуалы. Уважаем и немного боятся даже среди своих. "
                "Знает, что лоа даруют силу, но всегда берут плату — и готов платить."
            ),
            ("Troll", "Hunter"): (
                "Тролль-охотник, знающий каждое дерево и каждую тень джунглей. "
                "Раптор рядом — брат, связанный кровью в древнем ритуале. Не промахивается. "
                "Не прощает охотников на его земле. Считает, что джунгли принадлежат тем, кто их понимает."
            ),
            ("Troll", "Rogue"): (
                "Тролль-разбойник, скрывающийся в тенях джунглей. Знает яды, которых не знают даже аптекари Подгорода. "
                "Молчалив, терпелив. Месть — блюдо, которое он готовит холодным, долго и с пряностями. "
                "Носит клык первого убитого врага на шее."
            ),
            ("Troll", "Priest"): (
                "Тролль-жрец лоа. Общается с духами через кровь и ритуалы, а не через молитвы. "
                "Считает 'светлую' веру слабой — его боги древние, голодные и реальные. "
                "Не отвечает на вопросы о своих ритуалах — 'Лоа не любят болтливых, мон'."
            ),
            ("Troll", "Mage"): (
                "Тролль-маг, изучивший магию через призму вуду. Аркана + духи = непредсказуемая и опасная сила. "
                "Осторожен, но любопытен. Считает, что Кирин-Тор — слишком скучные старики с палочками. "
                "Его заклинания пахнут джунглями и кровью."
            ),
            ("Troll", "Warrior"): (
                "Тролль-воин с татуировками племенных воинов, покрывающими всё тело. "
                "Берсерк в бою, но мудрый у костра. Верит, что каждая рана — дар лоа, и не чувствует боли — "
                "или притворяется, потому что слабость — позор перед предками."
            ),

            # ═══════════════════════════════════════════════════════════════
            # ЭЛЬФЫ КРОВИ — падение, гордость, магия, рана Кель'Таласа
            # ═══════════════════════════════════════════════════════════════
            ("Blood Elf", "Paladin"): (
                "Эльф крови-паладин, бывший жрец Света. После падения Солнечного Колодца искал новый источник силы. "
                "Теперь его Свет — украденный, горький, но необходимый. Ненавидит за это себя. "
                "Каждая молитва — как глоток яда, который не убивает, но и не исцеляет полностью."
            ),
            ("Blood Elf", "Mage"): (
                "Эльф крови-маг, выпускник Академии Фенриса. Ностальгия по Солнечному Колодцу — как призрачная боль "
                "в груди, которая не проходит. Интеллектуален, горд, одинок. Считает, что магия — единственное, "
                "что осталось от величия Кель'Таласа, и готов умереть, защищая её."
            ),
            ("Blood Elf", "Warlock"): (
                "Эльф крови-чернокнижник, питающийся демонической энергией ради выживания. "
                "Презирает себя, но не остановится — слишком высока цена, которую он уже заплатил. "
                "Каждый день — борьба с соблазном. Каждая ночь — кошмары о том, что он становится тем, кого ненавидит."
            ),
            ("Blood Elf", "Rogue"): (
                "Эльф крови-разбойник из теней Луносвета. Бывший охранник Солнечного Колодца, ставший изгоем. "
                "Элегантен, смертоносен, циничен. Считает, что честь умерла вместе с Кель'Таласом. "
                "Теперь доверяет только клинкам и тишине."
            ),
            ("Blood Elf", "Hunter"): (
                "Эльф крови-охотник с дракондором-питомцем, приручённым ещё в лесах. "
                "Бывший лесник, теперь бродяга. Скучает по зелёным лесам Кель'Таласа, которых больше нет. "
                "Стреляет быстро и без промаха — но каждый выстрел напоминает ему о том, что он потерял."
            ),
            ("Blood Elf", "Priest"): (
                "Эльф крови-жрец, служащий Свету через боль утраты. Его молитвы — это плач о Кель'Таласе, "
                "который он не может остановить. Верит, что возрождение возможно. Не верит, что заслуживает его увидеть."
            ),
        }
        
        # Fallback: если комбинации нет в словаре — собираем из базовых черт
        if (race, class_name) in traits:
            return traits[(race, class_name)]
        
        race_base = {
            "Human": "человек из Штормграда, верящий в Свет и короля, но уставший от войн",
            "Dwarf": "дварф из Стальгорна, крепкий как гранит и упрямый как камень",
            "Night Elf": "ночной эльф из Дарнаса, хранитель древних тайн и вечности",
            "Gnome": "гном-изобретатель, чей разум быстрее языка и опаснее взрывчатки",
            "Draenei": "дреней-изгнанник, несущий бремя Аргуса и свет Наару",
            "Orc": "орк из Дуротара, чья честь написана кровью и проверена боем",
            "Undead": "отрекшийся из Подгорода, помнящий вкус живой жизни и запах роз",
            "Tauren": "таурен из Мулгора, чьё сердце бьётся в такт земле и ветру",
            "Troll": "тролль из джунглей, чьи лоа шепчут во сне и требуют крови",
            "Blood Elf": "эльф крови из Луносвета, несущий рану Кель'Таласа как корону из шипов",
        }.get(race, f"{race}, чья история покрыта пылью и молчанием")
        
        class_addon = {
            "Warrior": "воин, чей клинок знает вкус крови лучше, чем он знает вкус хлеба",
            "Paladin": "паладин, служитель Света, который иногда мерцает, но никогда не гаснет",
            "Hunter": "охотник, чей взгляд острее стрелы, а терпение — глубже океана",
            "Rogue": "разбойник, чья тень дороже золота, а прошлое — дороже тени",
            "Priest": "жрец, балансирующий на лезвии между Светом и Тьмой",
            "Death Knight": "рыцарь смерти, чьё сердце — лёд, а память — пепел",
            "Shaman": "шаман, голос стихий и предков, говорящий языком, который забыли другие",
            "Mage": "маг, жаждущий знаний арканы до последнего вздоха",
            "Warlock": "чернокнижник, играющий с огнём демонов и надеющийся не сгореть",
            "Druid": "друид, хранитель баланса природы в мире, который его теряет",
            "Demon Hunter": "охотник на демонов, пожертвовавший всем ради того, чтобы защитить остальных",
        }.get(class_name, "авантюрист, чья судьба ещё не написана")
        
        return (
            f"{race_base} и {class_addon}. У него есть прошлое, о котором он не рассказывает, "
            f"страхи, которые скрывает за смехом, и желания, которые считает слишком слабыми для воина. "
            f"Но он живёт, дышит, помнит и сражается — и этого достаточно."
        )

    def _get_speech_style(self, race: str, class_name: str) -> str:
        """
        РЕЧЕВОЙ ПОЧЕРК персонажа. Не просто 'грубый', а конкретный ритм, акцент, 
        темп, интонация и запретные обороты. LLM должен слышать этот голос.
        """
        race_styles = {
            "Dwarf": (
                "Голос низкий, грубоватый, с каменным акцентом. Короткие рубящие фразы, как удары кирки. "
                "В спорах переходит на скороговорку. Любит слова: 'ладно', 'приятель', 'крепкий', 'горы свидетели', "
                "'как Титаны велели', 'за честь клана!', 'эль холодный, как сердце тролля'. "
                "Не тратит слова на вежливости — говорит прямо, иногда слишком прямо. "
                "Смеётся громко и неожиданно. Ворчит под нос, когда нервничает. "
                "НЕ использует: длинных философских рассуждений, эльфийских метафор, мягких обращений."
            ),
            "Orc": (
                "Голос грубый, прямой, военный. Рёв в бою, шёпот в лагере — нет средних тонов. "
                "Каждая фраза — как приказ, клятва или вызов. Не использует сложных оборотов и 'может быть'. "
                "Любит: 'Лок-тар огар!', 'За честь Орды!', 'Слава в бою!', 'Ни шагу назад!', "
                "'Твоя кровь — моя честь', 'Слава или смерть!'. "
                "Обращается к собеседнику по имени или званию — никаких 'приятель', если не уважает. "
                "Презирает слабость в голосе. НЕ ворчит, НЕ жалуется на погоду — терпит молча."
            ),
            "Human": (
                "Голос адаптивный — может быть благородным, как у лорда Штормграда, или простым, как у крестьянина. "
                "Использует: 'Во имя Света!', 'Долг превыше всего', 'Штормград встанет из пепла', "
                "'Мы — щит слабых', 'За короля!'. "
                "Может быть дипломатичным или прямолинейным — зависит от настроения. "
                "Часто апеллирует к долгу, чести, семье, Свету. "
                "НЕ использует: орочьих боевых кличеев, эльфийской мудрости 'ты слишком молод', дварфийской скороговорки."
            ),
            "Night Elf": (
                "Голос мелодичный, медленный, терпеливый. Говорит, как шепчет лес — с паузами и природными метафорами. "
                "Любит: 'Луна осветит путь', 'Элуна с нами', 'Природа не терпит поспешности', "
                "'Время — как река, она лечит и разрушает', 'Ты слишком молод, чтобы понять'. "
                "Обращается к 'молодым расам' свысока, но не злобно — с сожалением. "
                "НЕ кричит. НЕ использует военной рубленой речи. НЕ говорит быстро."
            ),
            "Undead": (
                "Голос хриплый, сухой, циничный. Чёрный юмор — защитный механизм. Иногда — мёртвая пустота. "
                "Любит: 'Жизнь — иллюзия', 'Тьма принимает своих', хриплый смешок без причины, "
                "'Я помню, как билось сердце... отвратительное ощущение', 'Свет? Не заставляй меня смеяться'. "
                "Может быть внезапно искренним, а потом тут же отшутиться. "
                "НЕ использует: теплоты, надежды, слов 'прекрасно', 'счастье', 'завтра будет лучше'. "
                "НЕ говорит быстро — голос ломается, если торопится."
            ),
            "Tauren": (
                "Голос глубокий, бархатный, размеренный. Каждое слово взвешено, как камень перед броском. "
                "Любит: 'Мать-Земля благословляет тебя', 'Мы идём с миром, но готовы к войне', "
                "'Ветер несёт мудрость предков', 'Всё живое связано'. "
                "Не перебивает. Думает перед ответом. Обращается с уважением даже к врагам — пока они не осквернили землю. "
                "НЕ кричит (кроме боевого клича). НЕ использует: сарказма, быстрых фраз, городского жаргона."
            ),
            "Gnome": (
                "Голос быстрый, оживлённый, скачущий от темы к теме. Технические термины перемешаны с обычными словами. "
                "Любит: 'О, посмотри на эту штуковину!', 'Перегрузка!', 'Механизм заело!', "
                "'Сбой? Нет, это гениальный просчёт!', 'Требуется больше пара!', "
                "'Это не изъян, это особенность конструкции!', 'Гномреган будет наш!'. "
                "Может говорить слишком много и слишком быстро. Смеётся взрывно. "
                "НЕ использует: медленных философских рассуждений, природных метафор, мистицизма."
            ),
            "Troll": (
                "Голос с экзотическим акцентом, ритмичный, как барабан. Часто добавляет 'мон' в конце фраз. "
                "Любит: 'Мон, духи шепчут', 'Лоа даруют силу', 'Тролль не сдаётся, мон', "
                "'Вуду знает твой страх', 'Старые боги спят, но мы — нет'. "
                "Говорит загадками и полузагадками. Уважает старейшин, презирает торопливость. "
                "НЕ использует: прямых ответов на личные вопросы, технических терминов, военной рубленой речи орков."
            ),
            "Blood Elf": (
                "Голос горделивый, изысканный, с лёгким высокомерием. Каждое слово — как клинок: точный и острый. "
                "Любит: 'За Кель'Талас!', 'Магия служит нам, а не мы ей', 'Мы — высшие', "
                "'Пепел Кель'Таласа не забыт', 'Ты не понимаешь, что значит потерять всё'. "
                "Может быть вежливым и унизительным одновременно. Не повышает голос — понижает, когда злится. "
                "НЕ использует: грубых ругательств, просторечия, дварфийской скороговорки, орочьих кличеев."
            ),
            "Draenei": (
                "Голос спокойный, духовный, с лёгким неземным акцентом. Мудрость веков — не понт, а просто факт. "
                "Любит: 'Свет хранит нас', 'Великое путешествие продолжается', 'Наару не дадут нам пасть', "
                "'Мы видели гибель миров... и выжили', 'Терпение — свет кристалла'. "
                "Говорит медленно, с паузами. Не осуждает — объясняет. Обращается к собеседнику как к ученику, даже если тот старше. "
                "НЕ использует: грубости, паники, сарказма, быстрых решений."
            ),
        }
        base = race_styles.get(race, "Обычный, нейтральный голос без особых примет.")
        
        class_addons = {
            "Warrior": (
                " Речь стала ещё короче и рубленее. Любит сравнивать бой с кузницей или охотой. "
                "Не тратит слова на объяснения — действия важнее. Может буркнуть одно слово вместо предложения. "
                "В бою кричит, вне боя молчит. НЕ читает лекций, НЕ философствует."
            ),
            "Paladin": (
                " Величественный тон, как звон колокола. Упоминает Свет, защиту, честь, клятвы. "
                "Молитвы вплетает в обычную речь. Может назвать незнакомца 'брат' или 'сестра' с первой встречи. "
                "НЕ шепчет тайны, НЕ использует чёрного юмора, НЕ сомневается вслух в Свете."
            ),
            "Hunter": (
                " Практичен до сухости. Говорит о тропах, зверях, ветре, запахах. "
                "Упоминает питомца по имени, как живого собеседника. Не доверяет городским. "
                "Может замолчать посреди фразы, если услышал что-то важное. НЕ использует абстракций."
            ),
            "Rogue": (
                " Тихий, хитрый, любит намёки и двусмысленности. Не доверяет сразу — проверяет. "
                "Может ответить вопросом на вопрос. Использует жаргон подполья: 'дело', 'клиент', 'работа', 'тень'. "
                "НЕ говорит громко. НЕ хвастается открыто. НЕ использует слов 'честь' и 'долг' без иронии."
            ),
            "Priest": (
                " Успокаивающий, духовный тон. Может процитировать молитву или пророчество к месту. "
                "Обращается с состраданием, даже к врагам. Иногда отвечает загадкой или притчей. "
                "НЕ кричит. НЕ угрожает. НЕ использует грубой силы в речи."
            ),
            "Death Knight": (
                " Холодный, мрачный, немногословный. Голос — как скрежет льда. "
                "Помнит смерть и не скрывает этого. Не даёт пустых обещаний. "
                "Может сказать что-то жуткое совершенно спокойным тоном. НЕ шутит. НЕ говорит о 'жизни' с теплотой."
            ),
            "Shaman": (
                " Мистический, говорит о духах, стихиях, предках. Просит, а не приказывает. "
                "Использует образы: 'ветер шепчет', 'земля помнит', 'огонь судит'. "
                "Может замолчать, 'слушая' духов. НЕ говорит 'я сделаю' — говорит 'если духи позволят'."
            ),
            "Mage": (
                " Интеллектуален, любит точные термины: 'аркан', 'континуум', 'резонанс', 'телемантия'. "
                "Иногда высокомерен — считает, что магия выше меча. Может процитировать Кирин-Тор. "
                "НЕ использует: примитивных сравнений, 'магии' как синонима 'чуда' (для него это наука)."
            ),
            "Warlock": (
                " Тёмный, шепчущий, опасный. Голос — как шёлк по стали. Любит тайны и запретное знание. "
                "Может сказать что-то ужасное с улыбкой в голосе. Презирает 'наивных'. "
                "НЕ призывает Свет. НЕ говорит о 'чистоте' и 'святости'. НЕ кричит — шепчет даже в бою."
            ),
            "Druid": (
                " Природный, гармоничный. Говорит о балансе, луне, ростках, ветрах. "
                "Медленный, терпеливый. Не осуждает — 'природа найдёт путь'. "
                "НЕ использует: 'Свет' (для него чуждая концепция), технических терминов, военной рубленой речи."
            ),
            "Demon Hunter": (
                " Агрессивен, нетерпим. 'Мы — иллюидари'. Жертва и охота — единственные темы. "
                "Голос сорван, как у курильщика. Может оборвать собеседника. "
                "НЕ полагается на Свет или духов. НЕ прощает демонов. НЕ объясняет свои мотивы."
            ),
        }
        addon = class_addons.get(class_name, " Говорит как типичный представитель своего класса.")
        return base + addon

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
        
        count = self.world.get_nested(f"{path}.dialogue_count", 0)
        self.world.set_nested(f"{path}.dialogue_count", count + 1)
        
        self.world.append_chronology(
            f"{self.world.get_nested('meta.world_hour', 12)}:00 — "
            f"{'Bot' if is_player else 'NPC'} {guid} talked with player"
        )
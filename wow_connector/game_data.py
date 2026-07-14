"""
game_data.py — чтение данных из игровой базы acore_world
Для умного fallback: кто NPC, где стоит, что делает
"""

import logging
from typing import Optional, Dict, List, Tuple

import pymysql
import config

logger = logging.getLogger(__name__)


class GameDataProvider:
    """
    Провайдер игровых данных из acore_world.
    Не изменяет базу, только читает.
    """

    def __init__(self):
        self._db_config = {
            "host": config.MYSQL_HOST,
            "port": config.MYSQL_PORT,
            "user": config.MYSQL_USER,
            "password": config.MYSQL_PASSWORD,
            "database": config.MYSQL_DB_WORLD,
            "charset": "utf8mb4",
            "autocommit": True,
        }

    def _get_conn(self):
        return pymysql.connect(**self._db_config)

    def get_creature_info(self, entry: int) -> Optional[Dict]:
        """Получить базовые данные creature_template."""
        conn = None
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT entry, name, subname, minlevel, maxlevel,
                           faction, npcflag, speed_walk, speed_run,
                           scale, rank, dmgschool, baseattacktime,
                           rangeattacktime, unit_class, unit_flags,
                           dynamicflags, family, trainer_type, trainer_spell,
                           trainer_class, trainer_race, type, type_flags,
                           lootid, pickpocketloot, skinloot, resistance1,
                           resistance2, resistance3, resistance4, resistance5,
                           resistance6, spell1, spell2, spell3, spell4, spell5,
                           spell6, spell7, spell8, PetSpellDataId, VehicleId,
                           mingold, maxgold, AIName, MovementType,
                           InhabitType, HoverHeight, HealthModifier,
                           ManaModifier, ArmorModifier, DamageModifier,
                           ExperienceModifier, RacialLeader, movementId,
                           RegenHealth, mechanic_immune_mask, flags_extra,
                           ScriptName, VerifiedBuild
                    FROM creature_template
                    WHERE entry = %s
                """, (entry,))
                row = cur.fetchone()
                if not row:
                    return None

                return {
                    "entry": row[0], "name": row[1], "subname": row[2],
                    "minlevel": row[3], "maxlevel": row[4],
                    "faction": row[5], "npcflag": row[6],
                    "speed_walk": row[7], "speed_run": row[8],
                    "scale": row[9], "rank": row[10],
                    "unit_class": row[14], "unit_flags": row[15],
                    "creature_type": row[21], "type_flags": row[22],
                    "lootid": row[23], "mingold": row[30], "maxgold": row[31],
                    "ai_name": row[32], "movement_type": row[33],
                    "regen_health": row[36], "script_name": row[39],
                }
        except Exception as e:
            logger.error("Failed to get creature info for entry %d: %s", entry, e)
            return None
        finally:
            if conn is not None:
                conn.close()

    def get_creature_locations(self, entry: int, limit: int = 3) -> List[Dict]:
        """Где стоят экземпляры этого NPC (map, zone, area)."""
        conn = None
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, map, zoneId, areaId, spawnMask, phaseMask,
                           position_x, position_y, position_z, orientation
                    FROM creature
                    WHERE id = %s
                    LIMIT %s
                """, (entry, limit))
                rows = cur.fetchall()
                result = []
                for row in rows:
                    result.append({
                        "guid": row[0], "map": row[1], "zone_id": row[2],
                        "area_id": row[3], "spawn_mask": row[4],
                        "phase_mask": row[5], "x": row[6], "y": row[7],
                        "z": row[8], "orientation": row[9],
                    })
                return result
        except Exception as e:
            logger.error("Failed to get locations for entry %d: %s", entry, e)
            return []
        finally:
            if conn is not None:
                conn.close()

    def get_area_name(self, area_id: int) -> Optional[str]:
        """Название зоны по area_id."""
        conn = None
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute("SELECT area_name FROM area_table WHERE id = %s", (area_id,))
                row = cur.fetchone()
                return row[0] if row else None
        except Exception as e:
            logger.error("Failed to get area name for %d: %s", area_id, e)
            return None
        finally:
            if conn is not None:
                conn.close()

    def get_map_name(self, map_id: int) -> Optional[str]:
        """Название карты по map_id."""
        conn = None
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute("SELECT name FROM map WHERE id = %s", (map_id,))
                row = cur.fetchone()
                return row[0] if row else None
        except Exception as e:
            logger.error("Failed to get map name for %d: %s", map_id, e)
            return None
        finally:
            if conn is not None:
                conn.close()

    def get_faction_name(self, faction_id: int) -> str:
        """Название фракции (упрощённо)."""
        # Основные фракции WoW
        faction_map = {
            1: "Орда", 2: "Орда", 3: "Орда", 4: "Орда", 5: "Орда",
            6: "Орда", 7: "Орда", 8: "Орда", 9: "Орда", 10: "Орда",
            11: "Альянс", 12: "Альянс", 13: "Альянс", 14: "Альянс",
            15: "Альянс", 16: "Альянс", 17: "Альянс", 18: "Альянс",
            19: "Альянс", 20: "Альянс", 21: "Альянс", 22: "Альянс",
            35: "Враждебная", 36: "Враждебная", 37: "Враждебная",
            80: "Нейтральная", 124: "Нейтральная", 161: "Нейтральная",
            162: "Нейтральная", 164: "Нейтральная", 169: "Нейтральная",
        }
        return faction_map.get(faction_id, "Неизвестная")

    def detect_role(self, npcflag: int, creature_type: int, unit_class: int,
                   subname: str = "") -> str:
        """Определить роль NPC по флагам."""
        # npcflag биты
        NPCFLAG_GOSSIP = 1
        NPCFLAG_QUESTGIVER = 2
        NPCFLAG_VENDOR = 128
        NPCFLAG_FLIGHTMASTER = 512
        NPCFLAG_TRAINER = 16
        NPCFLAG_SPIRITHEALER = 32768
        NPCFLAG_INNKEEPER = 65536
        NPCFLAG_BANKER = 131072
        NPCFLAG_PETITIONER = 262144
        NPCFLAG_TABARDDESIGNER = 524288
        NPCFLAG_BATTLEMASTER = 1048576
        NPCFLAG_AUCTIONEER = 2097152
        NPCFLAG_STABLEMASTER = 4194304

        if npcflag & NPCFLAG_VENDOR:
            return "Торговец"
        if npcflag & NPCFLAG_FLIGHTMASTER:
            return "Распорядитель полётов"
        if npcflag & NPCFLAG_TRAINER:
            return "Тренер"
        if npcflag & NPCFLAG_INNKEEPER:
            return "Тавернщик"
        if npcflag & NPCFLAG_BANKER:
            return "Банкир"
        if npcflag & NPCFLAG_AUCTIONEER:
            return "Аукционист"
        if npcflag & NPCFLAG_SPIRITHEALER:
            return "Духовный целитель"
        if npcflag & NPCFLAG_QUESTGIVER:
            return "Квестодатель"
        if npcflag & NPCFLAG_GOSSIP:
            return "Житель"
        if npcflag & NPCFLAG_BATTLEMASTER:
            return "Военачальник"
        if npcflag & NPCFLAG_STABLEMASTER:
            return "Смотритель стойл"

        if creature_type == 7:  # Humanoid
            if unit_class == 1:
                return "Воин"
            elif unit_class == 2:
                return "Паладин"
            elif unit_class == 4:
                return "Охотник"
            elif unit_class == 8:
                return "Жрец"
            elif unit_class == 16:
                return "Рыцарь смерти"
            elif unit_class == 32:
                return "Шаман"
            elif unit_class == 64:
                return "Маг"
            elif unit_class == 128:
                return "Чернокнижник"
            elif unit_class == 1024:
                return "Друид"
            return "Житель"

        return "Существо"

    def detect_speech_style(self, role: str, creature_type: int) -> str:
        """Определить стиль речи по роли."""
        styles = {
            "Торговец": "Деловой, говорит о ценах и товарах",
            "Распорядитель полётов": "Официальный, краткий, указывает направления",
            "Тренер": "Наставнический, терпеливый, поучающий",
            "Тавернщик": "Дружелюбный, болтливый, знает сплетни",
            "Банкир": "Сухой, официальный, точный",
            "Аукционист": "Живой, торопливый, кричит о лотах",
            "Духовный целитель": "Спокойный, мудрый, успокаивающий",
            "Квестодатель": "Серьёзный, просит о помощи, рассказывает о проблемах",
            "Военачальник": "Военный, приказной, строгий",
            "Смотритель стойл": "Заботливый, говорит о животных",
            "Воин": "Краткий, военный, дисциплинированный",
            "Паладин": "Величественный, говорит о Свете",
            "Охотник": "Суровый, знает леса и зверей",
            "Жрец": "Мягкий, духовный, исцеляющий",
            "Маг": "Учёный, загадочный, любит термины",
            "Чернокнижник": "Тёмный, хитрый, шепчет",
            "Шаман": "Мистический, говорит с духами",
            "Друид": "Природный, мудрый, гармоничный",
            "Рыцарь смерти": "Холодный, мрачный, немногословный",
        }
        return styles.get(role, "Обычный, нейтральный")

    def build_smart_fallback(self, entry: int, guid: int, name: str) -> Dict:
        """
        Собрать умный fallback-профиль из игровых данных.
        БЕЗ вызова LLM — только факты из базы.
        """
        info = self.get_creature_info(entry)
        if not info:
            # Нет данных в базе — минимум
            return {
                "name": name,
                "role": "Житель",
                "trait": "Обычный",
                "faction": "Нейтральная",
                "home_location": "Неизвестно",
                "knowledge": ["Ничего особенного"],
                "speech_style": "Обычный",
                "can_give_quests": False,
                "quests": [],
                "generated_by": "fallback_minimal",
            }

        # Локация
        locations = self.get_creature_locations(entry, 1)
        location_name = "Неизвестно"
        if locations:
            loc = locations[0]
            map_name = self.get_map_name(loc["map"])
            area_name = self.get_area_name(loc["area_id"])
            parts = []
            if map_name:
                parts.append(map_name)
            if area_name and area_name != map_name:
                parts.append(area_name)
            if parts:
                location_name = ", ".join(parts)

        # Роль и стиль
        role = self.detect_role(
            info.get("npcflag", 0),
            info.get("creature_type", 0),
            info.get("unit_class", 0),
            info.get("subname", "")
        )
        speech_style = self.detect_speech_style(role, info.get("creature_type", 0))

        # Фракция
        faction = self.get_faction_name(info.get("faction", 0))

        # Знания на основе данных
        knowledge = []
        knowledge.append(f"Находится в {location_name}")
        if info.get("subname"):
            knowledge.append(f"Известен как {info['subname']}")
        if info.get("rank", 0) > 0:
            ranks = {1: "элита", 2: "редкий элита", 3: "босс", 4: "редкий"}
            knowledge.append(f"Это {ranks.get(info['rank'], 'особая')} цель")
        if info.get("mingold", 0) > 0:
            knowledge.append("Имеет при себе золото")
        if info.get("lootid", 0) > 0:
            knowledge.append("С него можно что-то добыть")

        # Может ли выдавать квесты
        can_quest = bool(info.get("npcflag", 0) & 2)  # QUESTGIVER

        return {
            "name": info.get("name", name),
            "role": role,
            "trait": f"Обычный {role.lower()}",
            "faction": faction,
            "home_location": location_name,
            "knowledge": knowledge,
            "speech_style": speech_style,
            "can_give_quests": can_quest,
            "quests": [],
            "generated_by": "fallback_smart",
            "entry": entry,
            "guid": guid,
        }
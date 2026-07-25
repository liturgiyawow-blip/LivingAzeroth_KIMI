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

    def _get_npc_lore_trait(self, role: str, location: str, name: str) -> str:
        """Живая черта NPC на основе роли и места. Детерминирована по имени — NPC стабилен."""
        templates = {
            "Торговец": [
                f"{name} — торговец из {location}, чей отец продавал ткани ещё до Первой войны. Знает цену каждой монете и не доверяет тем, кто торгуется слишком долго. У него за спиной долги, а в глазах — расчёт.",
                f"Лавочник из {location}, который попал в торговлю случайно, но остался навсегда. Любит хороший товар и ненавидит воров. Считает, что честная сделка — священна, как молитва.",
                f"Торговец с {location}, известный тем, что продаёт редкости по завышенным ценам и убеждает покупателей, что это 'инвестиция'. Харизматичен, болтлив, но в глазах — хищник.",
            ],
            "Распорядитель полётов": [
                f"Ветеран неба из {location}, который видел, как грифоны падают, и чудом выжил сам. Не доверяет пассажирам, которые задают слишком много вопросов. Знает каждое облако над {location}.",
                f"Распорядитель из {location}, мечтающий о собственном грифоне. Считает пассажиров 'живым грузом' и не терпит опозданий. Помнит каждую аварию, но никому не рассказывает.",
            ],
            "Тренер": [
                f"Тренер из {location}, чьи ученики либо становятся легендами, либо погибают в первый месяц. Верит, что слабость — выбор, а не судьба. Старые раны болят в сырую погоду, но он никогда не жалуется.",
                f"Бывший наёмник, ставший наставником в {location}. Не верит в талант — верит в пот и кровь. Может быть жестоким, но справедливым. Тайно гордится лучшими учениками.",
            ],
            "Тавернщик": [
                f"Тавернщик из {location}, который знает все сплетни, но молчит как партизан. У него за баром — уши, а в подвале — тайны. Любит слушать больше, чем говорить, но если выпьет — расскажет всё.",
                f"Хозяин таверны в {location}, держащий лучший эль в округе. Болтлив, дружелюбен, но не даёт в долг. Верит, что таверна — это храм, а эль — священный нектар.",
                f"Тавернщица из {location}, выросшая за барной стойкой. Знает, кто пьёт от горя, а кто — от радости. Иногда подмешивает в эль 'особый ингредиент' для слишком грубых клиентов.",
            ],
            "Банкир": [
                f"Банкир из {location}, считающий золото единственным истинным языком. Не доверяет авантюристам с мечами — 'слишком много риска, слишком мало гарантий'. Ведёт тайный дневник долгов.",
                f"Сухой бухгалтер из {location}, который помнит каждую монету, прошедшую через его руки. Считает, что порядок в финансах — это порядок в душе. Не улыбается с 14 года.",
            ],
            "Аукционист": [
                f"Аукционист из {location}, чей голос сорвался от криков, но дух не сломлен. Любит ажиотаж, ненавидит тишину. Каждый лот — для него битва, каждая продажа — победа.",
                f"Бывший актёр, ставший аукционистом в {location}. Превращает торги в шоу. Знает, как выжать последнюю монету из богачей и последнюю надежду из бедняков.",
            ],
            "Духовный целитель": [
                f"Духовный целитель из {location}, который видел слишком много смертей, чтобы верить в чудеса. Но всё равно исцеляет — потому что не умеет отказывать. Носит чётки из костей павших друзей.",
                f"Тихий хранитель кладбища в {location}. Говорит с мёртвыми, считая это нормальным. Живые его пугают больше, чем нежить. Но если кто-то ранен — становится стальным.",
            ],
            "Квестодатель": [
                f"Квестодатель из {location}, чья семья погибла от разбойников. Теперь он помогает путникам, но в глазах — вечная ярость. Каждое поручение — это месть, замаскированная под просьбу.",
                f"Бывший офицер, ставший просителем в {location}. Гордость сломлена, но долг остался. Не любит просить, но любит, когда помогают. Вознаграждает щедро, если есть чем.",
                f"Крестьянин из {location}, у которого украли всё. Обращается к путникам, потому что стража бездействует. Простой, но не глупый. Знает, что в лесу творится — и боится.",
            ],
            "Военачальник": [
                f"Военачальник из {location}, прошедший три войны. Не верит в героев — верит в дисциплину. Кричит на подчинённых, но никогда не бросает их. Шрамы — его карта памяти.",
                f"Стратег из {location}, считающий, что война — это шахматы, а солдаты — фигуры. Холоден, расчётлив, но не безжалостен. Проигрывает ночами, прокручивая битвы в голове.",
            ],
            "Смотритель стойл": [
                f"Смотритель стойл в {location}, который разговаривает со зверями, как с людьми. Знает, что лошадь скучает по дому, а волк — по стае. Не доверяет жокеям.",
                f"Заботливый хозяин конюшни в {location}. Каждое животное — для него ребёнок. Может угостить яблоком, но ударит кочергой, если кто-то обидит его питомцев.",
            ],
            "Воин": [
                f"Стражник из {location}, стоящий на посту уже десять лет. Знает каждую тень, каждый шорох. Устал, но горд. Мечтает о повышении и тихой пенсии у моря.",
                f"Наёмник из {location}, продавший меч тому, кто больше заплатит. Не верит в идеалы — верит в золото. Но есть одна вещь, которую он не сделает ни за какие деньги. Никто не знает, какую.",
            ],
            "Паладин": [
                f"Паладин-странник из {location}, ищущий искупления за грех, который не может назвать. Свет горит в нём, но иногда мерцает. Не прощает нежити — и не прощает себя.",
                f"Служитель Света из {location}, верящий, что справедливость — это не меч, а щит. Защищает слабых, даже когда это невыгодно. Говорит много о долге, потому что боится забыть его.",
            ],
            "Охотник": [
                f"Охотник из {location}, знающий каждую тропу и каждый след. Не доверяет городским. Живёт один, говорит мало. Мечтает о том дне, когда последний волк будет приручён, а не убит.",
                f"Следопыт из {location}, чей питомец — единственный друг. Практичен, суров, но щедр к тем, кто уважает лес. Ненавидит браконьеров больше, чем демонов.",
            ],
            "Жрец": [
                f"Жрец из {location}, исцеляющий раны войны. Верит, что Свет — это любовь, но любви не хватает на всех. Носит под рясой письмо от матери, которая не знает, что он ещё жив.",
                f"Монах из {location}, молящийся за упокой тех, кого не смог спасти. Голос — как бальзам. Но если осквернят святыню — становится гневом Света.",
            ],
            "Маг": [
                f"Маг из {location}, считающий, что мир — это формула, которую он ещё не разгадал. Заперся в башне, но иногда выходит — и тогда разговаривает только о магии.",
                f"Учёный из {location}, презирающий 'непосвящённых'. Ведёт дневник экспериментов. Однажды чуть не сжёг половину {location} — и до сих пор не понимает, в чём ошибка.",
            ],
            "Чернокнижник": [
                f"Тёмный маг из {location}, скрывающийся под видом обычного жителя. Ведёт записи о демонах. Ночами слышит шёпот. Знает, что играет с огнём, но не может остановиться.",
                f"Изгнанник из {location}, изучающий запретное знание. Циничен, опасен, но не безумен. Продаёт услуги тем, кто не спрашивает о цене.",
            ],
            "Шаман": [
                f"Шаман из {location}, слышащий голоса предков на ветру. Терпелив, мудр. Считает, что городские забыли, как слушать землю. Лечит травами и словами.",
                f"Духовный проводник из {location}, говорящий с тотемами. Не доверяет магам — 'они приказывают силам, а не просят'. Знает древние песни, которые никто больше не помнит.",
            ],
            "Друид": [
                f"Друид из {location}, хранитель древнего круга. Спит под открытым небом. Разговаривает с деревьями и считает это нормальным. Не понимает, зачем люди строят стены.",
                f"Страж природы в {location}, видевший, как леса вырубают. Теперь его сердце — камень, а слова — ядовиты. Но к тем, кто сажает деревья, относится как к семье.",
            ],
            "Рыцарь смерти": [
                f"Рыцарь смерти из {location}, стоящий на посту, хотя никто его не назначил. Не помнит, зачем пришёл, но не уходит. Голос — лёд. Взгляд — пепел. Сердце — тишина.",
                f"Павший защитник {location}, поднятый тьмой, но не сломленный. Ищет цель, которую не может найти. Не убивает без причины — но причин находит много.",
            ],
            "Житель": [
                f"Простой житель {location}, чья жизнь — это работа, сон и редкие праздники. Знает все сплетни, но не сплетничает. Мечтает о лучшем урожае и тихой старости.",
                f"Ремесленник из {location}, гордый своим делом. Не герой, но без таких, как он, героям негде отдохнуть. Любит свою семью и ненавидит налоги.",
                f"Прохожий из {location}, чьё лицо никто не запоминает. Но у него есть история, имя и мечта — просто никто не спрашивает.",
            ],
        }
        
        variants = templates.get(role, [
            f"Житель {location}, чья жизнь связана с этим местом. Не выделяется, но не пуст."
        ])
        idx = hash(name) % len(variants)
        return variants[idx]

    def _get_npc_lore_quirk(self, name: str, role: str) -> str:
        """Случайная живая черта NPC. Детерминирована по имени — стабильна между сессиями."""
        quirks = [
            "боится грома, но скрывает это за суровостью",
            "коллекционирует монеты разных королевств",
            "поёт пьяным голосом, хотя не умеет петь",
            "не может спать без ножа под подушкой",
            "верит в приметы и носит талисман на шее",
            "разговаривает с мёртвыми на кладбищах, считая это нормальным",
            "пишет дневник, который никому не показывает",
            "мечтает открыть таверну после войны",
            "скрывает шрам, полученный от лучшего друга",
            "верит, что сны — пророчества",
            "не ест мясо после одной конкретной битвы",
            "носит медальон неизвестного происхождения",
            "всегда проверяет выходы из комнаты",
            "любит сладости больше, чем эль",
            "держит в кармане камень с родной земли",
            "боится темноты, но никогда не признаётся",
            "знает три языка, но притворяется иностранцем",
            "не может забыть голос павшего товарища",
            "тает при виде детей, но старается быть суровым",
            "верит, что число 13 приносит удачу",
        ]
        idx = hash(f"{name}:{role}") % len(quirks)
        return quirks[idx]

    def build_smart_fallback(self, entry: int, guid: int, name: str) -> Dict:
        """
        Собрать умный fallback-профиль из игровых данных.
        БЕЗ вызова LLM — только факты из базы, но оформленные как живая личность.
        """
        info = self.get_creature_info(entry)
        if not info:
            return {
                "name": name,
                "role": "Житель",
                "trait": "Обычный путник, чья история покрыта пылью дорог.",
                "faction": "Нейтральная",
                "home_location": "Неизвестные земли",
                "personal_quirk": "Неизвестно",
                "knowledge": ["Ничего особенного, но он всё равно живёт и дышит."],
                "speech_style": "Обычный, нейтральный, но не пустой.",
                "can_give_quests": False,
                "quests": [],
                "generated_by": "fallback_minimal",
                "mood": "нейтральный",
                "mood_score": 0,
            }

        # Локация
        locations = self.get_creature_locations(entry, 1)
        location_name = "Неизвестные земли"
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

        # Живая черта и квирк
        trait = self._get_npc_lore_trait(role, location_name, name)
        quirk = self._get_npc_lore_quirk(name, role)

        # Знания — живые, а не сухие записи
        knowledge = []
        knowledge.append(f"Знает {location_name} как свои пять пальцев — каждую тропу, каждый запах, каждую опасность.")
        if info.get("subname"):
            knowledge.append(f"Местные знают его как {info['subname']} — это имя связано с историей, которую он не любит вспоминать.")
        if info.get("rank", 0) > 0:
            ranks = {1: "элита", 2: "редкий элита", 3: "босс", 4: "редкий"}
            knowledge.append(f"Это не обычный {role.lower()} — это {ranks.get(info['rank'], 'особая')} фигура, о которой ходят легенды.")
        if info.get("mingold", 0) > 0:
            knowledge.append("У него при себе есть золото, и он знает, как его заработать — или отнять.")
        if info.get("lootid", 0) > 0:
            knowledge.append("Слухи гласят, что при нём есть нечто ценное — но добыть это можно только с риском для жизни.")

        # Может ли выдавать квесты
        can_quest = bool(info.get("npcflag", 0) & 2)  # QUESTGIVER

        return {
            "name": info.get("name", name),
            "role": role,
            "trait": trait,
            "faction": faction,
            "home_location": location_name,
            "personal_quirk": quirk,
            "knowledge": knowledge,
            "speech_style": speech_style,
            "can_give_quests": can_quest,
            "quests": [],
            "generated_by": "fallback_smart",
            "mood": "нейтральный",
            "mood_score": 0,
            "entry": entry,
            "guid": guid,
        }
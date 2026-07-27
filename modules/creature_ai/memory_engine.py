"""
memory_engine.py — Иерархическая память ботов (Hierarchical Memory System)

5 слоёв:
  L0 Core      — кто ты, откуда, чего хочешь, чего боишься
  L1 Working   — где сейчас, что происходит вокруг
  L2 Episodic  — что с тобой случилось (травмы, победы, предательства)
  L3 Semantic  — что ты знаешь (лор, сплетни, ремесло)
  L4 Social    — кто тебе друг, кто враг, кто должен золото

Поиск: эвристика + FULLTEXT + теги. Без тяжёлых векторов.
"""

import json
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Dict, List, Optional

import pymysql

import config

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# КОНФИГУРАЦИЯ ПОИСКА
# ═══════════════════════════════════════════════════════════════════

@dataclass
class MemoryConfig:
    max_context_chars: int = 400      # жёсткий лимит в промпт (в бою не перегружаем)
    l2_episodes_limit: int = 2        # сколько эпизодов взять
    l3_facts_limit: int = 1           # сколько фактов взять (в бою мало)
    l4_social_limit: int = 1          # сколько социальных записей
    cache_ttl_sec: float = 60.0       # кэш retrieve на 60 секунд


# ═══════════════════════════════════════════════════════════════════
# ТРИГГЕРЫ: слово игрока → теги для поиска
# ═══════════════════════════════════════════════════════════════════

_TOPIC_TRIGGERS: Dict[str, List[str]] = {
    # --- Локации ---
    "нордскол": ["нордскол", "ледяной", "арктика", "снег", "вьюга"],
    "штормград": ["штормград", "вариан", "альянс", "король"],
    "оргриммар": ["оргриммар", "тралл", "орда", "калимдор"],
    "подгород": ["подгород", "сильвана", "отрекшиеся"],
    "дарнас": ["дарнас", "телдрассил", "элуна", "ночные_эльфы"],
    "стальгорд": ["стальгорд", "дворфы", "титаны", "горы"],
    "элвинн": ["элвинн", "голдшир", "штормград", "лес"],
    # --- Фракции / угрозы ---
    "плеть": ["плеть", "артас", "король_лич", "нежить", "чума", "скелет"],
    "орда": ["орда", "гром", "тарен", "клан"],
    "альянс": ["альянс", "свет", "лордерон"],
    "легион": ["легион", "демон", "скверна", "саргерас"],
    # --- Личное ---
    "семья": ["мать", "отец", "брат", "сестра", "родители", "сын", "дочь"],
    "дом": ["дом", "родина", "деревня", "ферма", "очаг"],
    "страх": ["боюсь", "страшно", "ужас", "кошмар", "тревога"],
    # --- Магия / религия ---
    "свет": ["свет", "наару", "паладин", "жрец", "утер"],
    "элун": ["элун", "луна", "ночной_эльф", "друид"],
    "лоа": ["лоа", "тролль", "вуду", "духи", "шаман"],
    "титаны": ["титан", "дворф", "создатели", "камень"],
    # --- Ремесла / быт ---
    "эл": ["эл", "пиво", "пьяный", "таверна", "градус"],
    "кузня": ["кузня", "ковка", "меч", "броня", "сталь"],
    "охота": ["охота", "дичь", "мясо", "шкуры", "следы"],
    # --- Боевые ---
    "бой": ["бой", "битва", "сражение", "война", "кровь"],
    "поражение": ["поражение", "вайп", "падение", "гибель", "потеря"],
    "победа": ["победа", "триумф", "возмездие", "месть"],
    "босс": ["босс", "главарь", "вождь", "повелитель"],
}


class BotMemoryEngine:
    """
    Единая точка доступа к памяти бота.
    Потокобезопасен на уровне БД (каждый вызов — своё соединение).
    """

    def __init__(self, cfg: MemoryConfig = None):
        self.cfg = cfg or MemoryConfig()
        self._db = {
            "host": config.MYSQL_HOST,
            "port": config.MYSQL_PORT,
            "user": config.MYSQL_USER,
            "password": config.MYSQL_PASSWORD,
            "database": config.MYSQL_DB_AI,
            "charset": "utf8mb4",
            "autocommit": True,
        }
        # LRU-кэш retrieve: ключ (bot_guid, query_hash) → (timestamp, text)
        self._cache: OrderedDict[str, tuple] = OrderedDict()
        self._cache_maxsize = 200

    # ═══════════════════════════════════════════════════════════════
    # ПУБЛИЧНЫЙ API
    # ═══════════════════════════════════════════════════════════════

    def retrieve(self, bot_guid: int, player_message: str,
                 player_name: str = "", player_guid: int = 0,
                 bot_race: str = "", bot_class: str = "") -> str:
        """
        Главный метод: по сообщению игрока собрать релевантный контекст.
        Возвращает готовый текст для вставки в system_prompt.
        """
        start = time.time()
        cache_key = f"{bot_guid}:{hash(player_message + bot_race + bot_class)}"

        # 1. Проверка кэша
        now = time.time()
        if cache_key in self._cache:
            ts, text = self._cache[cache_key]
            if now - ts < self.cfg.cache_ttl_sec:
                logger.debug("Memory cache HIT for bot %d", bot_guid)
                return text
            else:
                del self._cache[cache_key]

        # 2. Извлекаем теги
        tags = self._extract_tags((player_message + " " + bot_race + " " + bot_class).lower())

        # 3. Собираем слои (с graceful fallback если таблицы пусты/отсутствуют)
        core = self._safe_fetch(lambda: self._fetch_core(bot_guid))
        working = self._safe_fetch(lambda: self._fetch_working(bot_guid))
        episodes = self._safe_fetch(lambda: self._fetch_episodes(bot_guid, tags))
        facts = self._safe_fetch(lambda: self._fetch_semantic(bot_guid, tags))
        social = self._safe_fetch(lambda: self._fetch_social(bot_guid, player_guid, player_name))

        # 4. Компилируем
        context = self._compile(core, working, episodes, facts, social)

        # 5. Кладём в кэш
        self._cache[cache_key] = (now, context)
        if len(self._cache) > self._cache_maxsize:
            self._cache.popitem(last=False)

        elapsed = (time.time() - start) * 1000
        logger.debug("Memory retrieve for bot %d in %.1f ms | tags=%s",
                     bot_guid, elapsed, tags)
        return context

    def record_episode(self, bot_guid: int, episode_type: str, title: str,
                       summary: str, location: str = None, subzone: str = None,
                       involved: List[str] = None, emotional_tag: str = "нейтральный",
                       intensity: int = 50, is_trauma: bool = False,
                       is_boss: bool = False):
        """Записать событие в эпизодическую память."""
        involved_str = ";".join(involved) if involved else None
        sql = """
            INSERT INTO bot_episodic_memory
            (bot_guid, episode_type, title, summary, location, subzone,
             involved_entities, emotional_tag, intensity, is_trauma, is_boss, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, UNIX_TIMESTAMP())
        """
        self._execute(sql, (bot_guid, episode_type, title, summary,
                            location, subzone, involved_str, emotional_tag,
                            intensity, 1 if is_trauma else 0,
                            1 if is_boss else 0))
        logger.info("Episode recorded for bot %d: [%s] %s (boss=%s)",
                    bot_guid, episode_type, title, is_boss)

    def record_fact(self, bot_guid: int, domain: str, topic: str,
                    content: str, importance: int = 50,
                    certainty: str = "знаю", source: str = None):
        """Записать факт в семантическую память."""
        sql = """
            INSERT INTO bot_semantic_memory
            (bot_guid, domain, topic, content, importance, certainty, source, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, UNIX_TIMESTAMP())
        """
        self._execute(sql, (bot_guid, domain, topic, content,
                            importance, certainty, source))
        logger.info("Fact recorded for bot %d: [%s] %s", bot_guid, domain, topic)

    def update_social(self, bot_guid: int, target_guid: int, target_name: str,
                      target_type: str = "player", relationship: str = None,
                      trust_delta: int = 0, affection_delta: int = 0,
                      shared_note: str = None):
        """Обновить или создать социальную запись."""
        updates = []
        params = []
        if relationship:
            updates.append("relationship = %s")
            params.append(relationship)
        if trust_delta:
            updates.append("trust_level = LEAST(100, GREATEST(0, trust_level + %s))")
            params.append(trust_delta)
        if affection_delta:
            updates.append("affection_level = LEAST(100, GREATEST(-100, affection_level + %s))")
            params.append(affection_delta)
        if shared_note:
            updates.append("shared_history = CONCAT(COALESCE(shared_history,''), '\\n', %s)")
            params.append(shared_note)

        if updates:
            sql_upd = f"""
                UPDATE bot_social_memory
                SET {', '.join(updates)}, updated_at = UNIX_TIMESTAMP()
                WHERE bot_guid = %s AND target_guid = %s
            """
            params.extend([bot_guid, target_guid])
            affected = self._execute(sql_upd, tuple(params), fetch=False)
            if affected == 0:
                sql_ins = """
                    INSERT INTO bot_social_memory
                    (bot_guid, target_guid, target_name, target_type,
                     relationship, trust_level, affection_level, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, UNIX_TIMESTAMP())
                """
                self._execute(sql_ins, (bot_guid, target_guid, target_name,
                                        target_type, relationship or 'знакомый',
                                        50 + trust_delta, affection_delta))

    def update_working(self, bot_guid: int, **fields):
        """Обновить рабочую память (только переданные поля)."""
        allowed = {"current_zone", "current_subzone", "current_mood",
                   "current_goal", "active_threat", "party_members",
                   "last_combat_result", "last_npc_spoken"}
        updates = []
        params = []
        for k, v in fields.items():
            if k in allowed:
                updates.append(f"{k} = %s")
                params.append(v)
        if not updates:
            return
        sql = f"""
            INSERT INTO bot_working_memory (bot_guid, {', '.join(fields.keys())}, updated_at)
            VALUES (%s, {', '.join(['%s'] * len(params))}, UNIX_TIMESTAMP())
            ON DUPLICATE KEY UPDATE {', '.join(updates)}, updated_at = UNIX_TIMESTAMP()
        """
        self._execute(sql, (bot_guid, *params), fetch=False)

    def cleanup_episodes(self, bot_guid: int):
        """
        Агрессивная очистка:
        - Обычные бои (is_boss=0): оставить последние 20
        - Боссы (is_boss=1): оставить последние 10
        """
        # 1. Обычные
        sql_normal = """
            DELETE FROM bot_episodic_memory
            WHERE bot_guid = %s AND is_boss = 0
              AND id NOT IN (
                  SELECT id FROM (
                      SELECT id FROM bot_episodic_memory
                      WHERE bot_guid = %s AND is_boss = 0
                      ORDER BY created_at DESC LIMIT 20
                  ) tmp
              )
        """
        self._execute(sql_normal, (bot_guid, bot_guid), fetch=False)

        # 2. Боссы
        sql_boss = """
            DELETE FROM bot_episodic_memory
            WHERE bot_guid = %s AND is_boss = 1
              AND id NOT IN (
                  SELECT id FROM (
                      SELECT id FROM bot_episodic_memory
                      WHERE bot_guid = %s AND is_boss = 1
                      ORDER BY created_at DESC LIMIT 10
                  ) tmp
              )
        """
        self._execute(sql_boss, (bot_guid, bot_guid), fetch=False)
        logger.debug("Cleaned up old episodes for bot %d", bot_guid)

    # ═══════════════════════════════════════════════════════════════
    # ВНУТРЕННЯЯ ЛОГИКА
    # ═══════════════════════════════════════════════════════════════

    def _extract_tags(self, text: str) -> List[str]:
        tags = set()
        for topic, keywords in _TOPIC_TRIGGERS.items():
            for kw in keywords:
                if kw in text:
                    tags.add(topic)
                    break
        return list(tags)

    def _safe_fetch(self, fn):
        """Graceful fallback: если таблицы нет или ошибка — вернуть None/[] без краша."""
        try:
            return fn()
        except Exception as e:
            logger.warning("Memory fetch failed (table missing?): %s", e)
            return None

    def _fetch_core(self, bot_guid: int) -> Optional[Dict]:
        row = self._query_one(
            "SELECT full_name, homeland, family_status, life_goal, greatest_fear, secret, prized_possession "
            "FROM bot_core_memory WHERE bot_guid = %s", (bot_guid,)
        )
        if not row:
            return None
        return {
            "name": row[0], "homeland": row[1], "family": row[2],
            "goal": row[3], "fear": row[4], "secret": row[5], "item": row[6],
        }

    def _fetch_working(self, bot_guid: int) -> Optional[Dict]:
        row = self._query_one(
            "SELECT current_zone, current_subzone, current_mood, current_goal, "
            "active_threat, party_members, last_combat_result "
            "FROM bot_working_memory WHERE bot_guid = %s", (bot_guid,)
        )
        if not row:
            return None
        return {
            "zone": row[0], "subzone": row[1], "mood": row[2],
            "goal": row[3], "threat": row[4], "party": row[5], "combat": row[6],
        }

    def _fetch_episodes(self, bot_guid: int, tags: List[str]) -> List[Dict]:
        if tags:
            like_clauses = " OR ".join(["emotional_tag = %s"] * len(tags))
            sql = f"""
                SELECT title, summary, emotional_tag, intensity, is_boss
                FROM bot_episodic_memory
                WHERE bot_guid = %s AND ({like_clauses})
                ORDER BY is_boss DESC, intensity DESC, last_accessed DESC
                LIMIT %s
            """
            rows = self._query(sql, (bot_guid, *tags, self.cfg.l2_episodes_limit))
            if rows:
                return rows

        sql = """
            SELECT title, summary, emotional_tag, intensity, is_boss
            FROM bot_episodic_memory
            WHERE bot_guid = %s
            ORDER BY is_boss DESC, intensity DESC, last_accessed DESC
            LIMIT %s
        """
        return self._query(sql, (bot_guid, self.cfg.l2_episodes_limit))

    def _fetch_semantic(self, bot_guid: int, tags: List[str]) -> List[Dict]:
        if tags:
            topic_likes = " OR ".join(["topic LIKE %s"] * len(tags))
            sql = f"""
                SELECT domain, topic, content, importance
                FROM bot_semantic_memory
                WHERE bot_guid = %s AND ({topic_likes})
                ORDER BY importance DESC, last_accessed DESC
                LIMIT %s
            """
            rows = self._query(sql, (bot_guid, *[f"%{t}%" for t in tags], self.cfg.l3_facts_limit))
            if rows:
                return rows

        sql = """
            SELECT domain, topic, content, importance
            FROM bot_semantic_memory
            WHERE bot_guid = %s
            ORDER BY importance DESC, last_accessed DESC
            LIMIT %s
        """
        return self._query(sql, (bot_guid, self.cfg.l3_facts_limit))

    def _fetch_social(self, bot_guid: int, player_guid: int, player_name: str) -> Optional[Dict]:
        if not player_guid:
            return None
        row = self._query_one(
            "SELECT relationship, trust_level, affection_level, shared_history, first_met_location "
            "FROM bot_social_memory WHERE bot_guid = %s AND target_guid = %s",
            (bot_guid, player_guid)
        )
        if not row:
            return None
        return {
            "relationship": row[0], "trust": row[1], "affection": row[2],
            "history": row[3], "met_where": row[4],
        }

    def _compile(self, core, working, episodes, facts, social) -> str:
        lines = []

        # L0: Core
        if core:
            lines.append(f"Твоё имя — {core.get('name') or 'неизвестно'}.")
            if core.get("homeland"):
                lines.append(f"Родина: {core['homeland']}.")
            if core.get("family"):
                lines.append(f"Семья: {core['family']}.")
            if core.get("goal"):
                lines.append(f"Цель жизни: {core['goal']}.")
            if core.get("fear"):
                lines.append(f"Ты боишься: {core['fear']}.")
            if core.get("secret"):
                lines.append(f"Тайна: {core['secret']}.")
            if core.get("item"):
                lines.append(f"Самая ценная вещь: {core['item']}.")

        # L1: Working
        if working:
            w = []
            if working.get("zone"):
                w.append(f"сейчас в {working['zone']}")
            if working.get("subzone"):
                w.append(f"район — {working['subzone']}")
            if working.get("mood"):
                w.append(f"настроение — {working['mood']}")
            if working.get("goal"):
                w.append(f"цель: {working['goal']}")
            if working.get("threat") and working["threat"] != "нет":
                w.append(f"угроза: {working['threat']}")
            if working.get("party"):
                try:
                    party = json.loads(working["party"]) if isinstance(working["party"], str) else working["party"]
                    if party:
                        w.append(f"рядом: {', '.join(party)}")
                except Exception:
                    pass
            if w:
                lines.append("Сейчас: " + ", ".join(w) + ".")

        # L4: Social
        if social:
            rel = social.get("relationship", "знакомый")
            trust = social.get("trust", 50)
            aff = social.get("affection", 0)
            lines.append(f"Отношения с собеседником: {rel} (доверие {trust}, расположение {aff}).")
            if social.get("history"):
                lines.append(f"Ваша общая история: {social['history'][:120]}")

        # L2: Episodic
        if episodes:
            lines.append("Вспоминаешь:")
            for ep in episodes:
                boss_mark = " [ЭПИЧНО]" if ep.get("is_boss") else ""
                lines.append(f"— {ep['summary']}{boss_mark}")

        # L3: Semantic
        if facts:
            lines.append("Знаешь:")
            for f in facts:
                lines.append(f"— {f['content']}")

        text = "\n".join(lines)
        if len(text) > self.cfg.max_context_chars:
            text = text[:self.cfg.max_context_chars - 3] + "..."
        return text

    # ═══════════════════════════════════════════════════════════════
    # DB HELPERS
    # ═══════════════════════════════════════════════════════════════

    def _query(self, sql: str, params: tuple) -> List[Dict]:
        conn = pymysql.connect(**self._db)
        rows = []
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                cols = [d[0] for d in cur.description] if cur.description else []
                for r in cur.fetchall():
                    rows.append(dict(zip(cols, r)))
        except Exception as e:
            logger.error("Memory query error: %s", e)
        finally:
            conn.close()
        return rows

    def _query_one(self, sql: str, params: tuple) -> Optional[tuple]:
        conn = pymysql.connect(**self._db)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchone()
        except Exception as e:
            logger.error("Memory query_one error: %s", e)
            return None
        finally:
            conn.close()

    def _execute(self, sql: str, params: tuple, fetch: bool = False):
        conn = pymysql.connect(**self._db)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                if fetch:
                    cols = [d[0] for d in cur.description] if cur.description else []
                    return [dict(zip(cols, r)) for r in cur.fetchall()]
                conn.commit()
                return cur.rowcount
        except Exception as e:
            logger.error("Memory execute error: %s", e)
            return 0
        finally:
            conn.close()
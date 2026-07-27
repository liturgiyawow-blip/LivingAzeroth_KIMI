"""
memory_loader.py — Загрузка/перезагрузка предысторий ботов из JSON

При старте main.py читает data/bot_backstories.json
и заливает в MySQL. Core перезаписывается (ON DUPLICATE KEY UPDATE).
Semantic дописывается (или чистится при force_reload).
"""

import json
import logging
from pathlib import Path

import pymysql

import config

logger = logging.getLogger(__name__)


class MemoryLoader:
    def __init__(self, backstory_file: Path = None):
        self.file = backstory_file or (config.DATA_DIR / "bot_backstories.json")
        self._db = {
            "host": config.MYSQL_HOST,
            "port": config.MYSQL_PORT,
            "user": config.MYSQL_USER,
            "password": config.MYSQL_PASSWORD,
            "database": config.MYSQL_DB_AI,
            "charset": "utf8mb4",
            "autocommit": True,
        }

    def load_all(self, force_reload: bool = False):
        """Загрузить все предыстории из JSON в БД."""
        if not self.file.exists():
            logger.warning("Backstory file not found: %s", self.file)
            return

        try:
            with open(self.file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.error("Failed to parse backstories JSON: %s", e)
            return

        for entry in data.get("backstories", []):
            try:
                self._load_bot(entry, force_reload)
            except Exception as e:
                logger.error("Failed to load backstory for bot %s: %s",
                             entry.get("bot_guid"), e)

        logger.info("MemoryLoader finished. File: %s", self.file)

    def _load_bot(self, entry: dict, force_reload: bool):
        bot_guid = entry.get("bot_guid")
        if not bot_guid:
            logger.warning("Skipping backstory without bot_guid")
            return

        core = entry.get("core", {})
        semantic = entry.get("semantic", [])

        if core:
            self._upsert_core(bot_guid, core)
            logger.info("Core memory loaded for bot %d (%s)",
                        bot_guid, core.get("full_name", "???"))

        if semantic:
            if force_reload:
                self._clear_semantic(bot_guid)
            for fact in semantic:
                self._insert_semantic(bot_guid, fact)
            logger.info("Loaded %d semantic facts for bot %d", len(semantic), bot_guid)

    def _upsert_core(self, bot_guid: int, core: dict):
        fields = [
            "full_name", "homeland", "family_status", "life_goal",
            "greatest_fear", "secret", "prized_possession"
        ]
        cols = []
        vals = []
        updates = []
        for f in fields:
            if f in core:
                cols.append(f)
                vals.append(core[f])
                updates.append(f"{f} = %s")

        if not cols:
            return

        sql = (
            f"INSERT INTO bot_core_memory (bot_guid, {', '.join(cols)}, created_at) "
            f"VALUES (%s, {', '.join(['%s'] * len(vals))}, UNIX_TIMESTAMP()) "
            f"ON DUPLICATE KEY UPDATE {', '.join(updates)}, updated_at = UNIX_TIMESTAMP()"
        )
        # Для ON DUPLICATE KEY UPDATE передаём vals ещё раз
        self._execute(sql, (bot_guid, *vals, *vals))

    def _clear_semantic(self, bot_guid: int):
        self._execute(
            "DELETE FROM bot_semantic_memory WHERE bot_guid = %s",
            (bot_guid,)
        )

    def _insert_semantic(self, bot_guid: int, fact: dict):
        sql = (
            "INSERT INTO bot_semantic_memory "
            "(bot_guid, domain, topic, content, importance, certainty, source, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, UNIX_TIMESTAMP())"
        )
        self._execute(sql, (
            bot_guid,
            fact.get("domain", "lore"),
            fact.get("topic", "general"),
            fact.get("content", ""),
            fact.get("importance", 50),
            fact.get("certainty", "знаю"),
            fact.get("source", "backstory"),
        ))

    def _execute(self, sql: str, params: tuple):
        conn = pymysql.connect(**self._db)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                conn.commit()
        except Exception as e:
            logger.error("MemoryLoader SQL error: %s", e)
            raise
        finally:
            conn.close()
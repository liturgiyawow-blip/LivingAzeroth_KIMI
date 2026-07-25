"""
Database Cleanup — автоматическая очистка старых диалогов и запросов

Удаляет:
- ai_requests старше N дней (по умолчанию 7)
- ai_responses старше N дней (по умолчанию 7)
- POST-COMBAT ai_requests старше 6 часов (агрессивно, тяжёлые JSON)
- npc_memory/bot_memory старше N дней (опционально)

Запускается раз в сутки в фоновом потоке.
"""

import logging
import threading
import time
from datetime import datetime, timedelta

import config
import pymysql

logger = logging.getLogger(__name__)


class DatabaseCleaner:
    """Периодическая очистка БД от старых записей."""

    def __init__(
        self,
        cleanup_interval_hours: int = 24,
        retention_days_requests: int = 7,
        retention_days_memory: int = 30,
        cleanup_memory: bool = False,
    ):
        """
        :param cleanup_interval_hours: Интервал между очистками (часов)
        :param retention_days_requests: Хранить ai_requests/ai_responses не дольше N дней
        :param retention_days_memory: Хранить npc_memory/bot_memory не дольше N дней
        :param cleanup_memory: Удалять ли также старую память? (Осторожно!)
        """
        self.cleanup_interval_hours = cleanup_interval_hours
        self.retention_days_requests = retention_days_requests
        self.retention_days_memory = retention_days_memory
        self.cleanup_memory = cleanup_memory
        self._running = True
        self._timer = None

    def start(self):
        """Запустить фоновый поток очистки."""
        self._run_cleanup_loop()
        logger.info(
            "DatabaseCleaner started (interval: %dh)", self.cleanup_interval_hours
        )

    def _run_cleanup_loop(self):
        """Бесконечный цикл очистки."""

        def _tick():
            if not self._running:
                return

            try:
                self.cleanup()
            except Exception as e:
                logger.error("Cleanup error: %s", e)
            finally:
                if self._running:
                    self._timer = threading.Timer(
                        self.cleanup_interval_hours * 3600, _tick
                    )
                    self._timer.daemon = True
                    self._timer.start()

        # Первый запуск через 60 секунд, чтобы не забить БД сразу после старта
        self._timer = threading.Timer(60, _tick)
        self._timer.daemon = True
        self._timer.start()

    def cleanup(self):
        """Выполнить очистку БД."""
        logger.info("═" * 60)
        logger.info("Starting database cleanup...")
        logger.info("═" * 60)

        # Пороги времени
        cutoff_requests = datetime.now() - timedelta(days=self.retention_days_requests)
        cutoff_memory = datetime.now() - timedelta(days=self.retention_days_memory)

        # 1. Агрессивно: POST-COMBAT (тяжёлые JSON)
        deleted_post_combat = self._cleanup_post_combat()
        # 1.5. Зависшие ответы (fetched=0, не забраны игроком)
        deleted_stale = self._cleanup_stale_responses()
        # 2. Обычная чистка: диалоги и ответы
        deleted_requests = self._cleanup_requests(cutoff_requests)
        deleted_responses = self._cleanup_responses(cutoff_requests)

        # 3. Опционально: память
        if self.cleanup_memory:
            deleted_npc_mem = self._cleanup_npc_memory(cutoff_memory)
            deleted_bot_mem = self._cleanup_bot_memory(cutoff_memory)
        else:
            deleted_npc_mem = 0
            deleted_bot_mem = 0

        logger.info("Cleanup finished:")
        logger.info(
            "  - POST-COMBAT: %d rows deleted (aggressive 6h)",
            deleted_post_combat,
        )
        logger.info(
            "  - stale responses: %d rows deleted (fetched=0, older than 2h)",
            deleted_stale,
        )
        logger.info(
            "  - ai_requests: %d rows deleted (older than %d days)",
            deleted_requests,
            self.retention_days_requests,
        )
        logger.info(
            "  - ai_responses: %d rows deleted (older than %d days)",
            deleted_responses,
            self.retention_days_requests,
        )
        if self.cleanup_memory:
            logger.info(
                "  - npc_memory: %d rows deleted (older than %d days)",
                deleted_npc_mem,
                self.retention_days_memory,
            )
            logger.info(
                "  - bot_memory: %d rows deleted (older than %d days)",
                deleted_bot_mem,
                self.retention_days_memory,
            )
        logger.info("═" * 60)

    def _cleanup_post_combat(self) -> int:
        """Удалить старые POST-COMBAT запросы (агрессивно, 6 часов)."""
        cutoff = datetime.now() - timedelta(hours=6)
        cutoff_ts = int(cutoff.timestamp())
        conn = None
        try:
            conn = pymysql.connect(
                host=config.MYSQL_HOST,
                port=config.MYSQL_PORT,
                user=config.MYSQL_USER,
                password=config.MYSQL_PASSWORD,
                database=config.MYSQL_DB_CHARACTERS,
                charset="utf8mb4",
                autocommit=True,
            )
            with conn.cursor() as cur:
                sql = (
                    "DELETE FROM ai_requests "
                    "WHERE channel_type = 'POST-COMBAT' AND created_at < %s"
                )
                cur.execute(sql, (cutoff_ts,))
                deleted = cur.rowcount
                if deleted > 0:
                    logger.info(
                        "Deleted %d POST-COMBAT rows (older than 6h)", deleted
                    )
                return deleted
        except Exception as e:
            logger.error("Failed to cleanup POST-COMBAT: %s", e)
            return 0
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def _cleanup_stale_responses(self) -> int:
        """Удалить 'зависшие' ответы (fetched=0 старше 2 часов)."""
        cutoff = datetime.now() - timedelta(hours=2)
        cutoff_ts = int(cutoff.timestamp())
        conn = None
        try:
            conn = pymysql.connect(
                host=config.MYSQL_HOST,
                port=config.MYSQL_PORT,
                user=config.MYSQL_USER,
                password=config.MYSQL_PASSWORD,
                database=config.MYSQL_DB_CHARACTERS,
                charset="utf8mb4",
                autocommit=True,
            )
            with conn.cursor() as cur:
                sql = "DELETE FROM ai_responses WHERE fetched = 0 AND created_at < %s"
                cur.execute(sql, (cutoff_ts,))
                deleted = cur.rowcount
                if deleted > 0:
                    logger.info(
                        "Deleted %d stale responses (fetched=0, older than 2h)",
                        deleted,
                    )
                return deleted
        except Exception as e:
            logger.error("Failed to cleanup stale responses: %s", e)
            return 0
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def _cleanup_requests(self, cutoff_dt: datetime) -> int:
        """Удалить старые ai_requests."""
        cutoff_ts = int(cutoff_dt.timestamp())
        conn = None
        try:
            conn = pymysql.connect(
                host=config.MYSQL_HOST,
                port=config.MYSQL_PORT,
                user=config.MYSQL_USER,
                password=config.MYSQL_PASSWORD,
                database=config.MYSQL_DB_CHARACTERS,
                charset="utf8mb4",
                autocommit=True,
            )
            with conn.cursor() as cur:
                sql = "DELETE FROM ai_requests WHERE created_at < %s"
                cur.execute(sql, (cutoff_ts,))
                deleted = cur.rowcount
                logger.debug("Deleted %d rows from ai_requests", deleted)
                return deleted
        except Exception as e:
            logger.error("Failed to cleanup ai_requests: %s", e)
            return 0
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def _cleanup_responses(self, cutoff_dt: datetime) -> int:
        """Удалить старые ai_responses."""
        cutoff_ts = int(cutoff_dt.timestamp())
        conn = None
        try:
            conn = pymysql.connect(
                host=config.MYSQL_HOST,
                port=config.MYSQL_PORT,
                user=config.MYSQL_USER,
                password=config.MYSQL_PASSWORD,
                database=config.MYSQL_DB_CHARACTERS,
                charset="utf8mb4",
                autocommit=True,
            )
            with conn.cursor() as cur:
                sql = "DELETE FROM ai_responses WHERE created_at < %s"
                cur.execute(sql, (cutoff_ts,))
                deleted = cur.rowcount
                logger.debug("Deleted %d rows from ai_responses", deleted)
                return deleted
        except Exception as e:
            logger.error("Failed to cleanup ai_responses: %s", e)
            return 0
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def _cleanup_npc_memory(self, cutoff_dt: datetime) -> int:
        """Удалить старые npc_memory записи."""
        cutoff_ts = int(cutoff_dt.timestamp())
        conn = None
        try:
            conn = pymysql.connect(
                host=config.MYSQL_HOST,
                port=config.MYSQL_PORT,
                user=config.MYSQL_USER,
                password=config.MYSQL_PASSWORD,
                database=config.MYSQL_DB_AI,
                charset="utf8mb4",
                autocommit=True,
            )
            with conn.cursor() as cur:
                sql = "DELETE FROM npc_memory WHERE created_at < %s"
                cur.execute(sql, (cutoff_ts,))
                deleted = cur.rowcount
                logger.debug("Deleted %d rows from npc_memory", deleted)
                return deleted
        except Exception as e:
            logger.error("Failed to cleanup npc_memory: %s", e)
            return 0
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def _cleanup_bot_memory(self, cutoff_dt: datetime) -> int:
        """Удалить старые bot_memory записи."""
        cutoff_ts = int(cutoff_dt.timestamp())
        conn = None
        try:
            conn = pymysql.connect(
                host=config.MYSQL_HOST,
                port=config.MYSQL_PORT,
                user=config.MYSQL_USER,
                password=config.MYSQL_PASSWORD,
                database=config.MYSQL_DB_AI,
                charset="utf8mb4",
                autocommit=True,
            )
            with conn.cursor() as cur:
                sql = "DELETE FROM bot_memory WHERE created_at < %s"
                cur.execute(sql, (cutoff_ts,))
                deleted = cur.rowcount
                logger.debug("Deleted %d rows from bot_memory", deleted)
                return deleted
        except Exception as e:
            logger.error("Failed to cleanup bot_memory: %s", e)
            return 0
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def shutdown(self):
        """Остановить очистку."""
        self._running = False
        if self._timer:
            self._timer.cancel()
        logger.info("DatabaseCleaner stopped")
"""
WoWDBBridge — MySQL-based bridge between Python and WoW Eluna
Replaces file-based polling with direct MySQL queries.
"""

import time
import threading
import logging
from typing import Callable, List

import pymysql

import config

logger = logging.getLogger(__name__)


class WoWDBBridge:
    """
    Читает ai_requests из MySQL, отправляет в обработчики,
    пишет ai_responses обратно в MySQL.
    """
    
    def __init__(self):
        self._running = True
        self._callbacks: List[Callable] = []
        self._last_request_id = 0
        
        # MySQL connection params from config
        self._db_config = {
            "host": config.MYSQL_HOST,
            "port": config.MYSQL_PORT,
            "user": config.MYSQL_USER,
            "password": config.MYSQL_PASSWORD,
            "database": config.MYSQL_DB_CHARACTERS,
            "charset": "utf8mb4",
            "autocommit": True,
        }
        
        # Test connection on init
        self._test_connection()
        
        # FIX: не обрабатывать старые запросы после рестарта
        self._init_last_id()
    
    def _get_conn(self):
        """Создать новое соединение (thread-safe)."""
        return pymysql.connect(**self._db_config)
    
    def _test_connection(self):
        """Проверить подключение при старте."""
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            conn.close()
            logger.info("MySQL bridge connected: %s:%d/%s", 
                       config.MYSQL_HOST, config.MYSQL_PORT, config.MYSQL_DB_CHARACTERS)
        except Exception as e:
            logger.error("MySQL connection failed: %s", e)
            raise
    
    def _init_last_id(self):
        """
        FIX: При старте смотрим MAX(id) в ai_requests.
        Иначе после рестарта сервер обработает все старые необработанные запросы.
        """
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(id) as max_id FROM ai_requests")
                row = cur.fetchone()
                self._last_request_id = row[0] or 0
                logger.info("Last request id set to %d", self._last_request_id)
            conn.close()
        except Exception as e:
            logger.error("Failed to init last_request_id: %s", e)
            self._last_request_id = 0
    
    def start(self):
        """Запустить фоновый polling новых запросов."""
        threading.Thread(target=self._poll_loop, daemon=True, name="DBBridgePoller").start()
        logger.info("DB Bridge polling started")
    
    def register_callback(self, callback: Callable):
        """Зарегистрировать обработчик новых запросов."""
        self._callbacks.append(callback)
        logger.debug("Callback registered, total: %d", len(self._callbacks))
    
    def _poll_loop(self):
        """Основной цикл: читать ai_requests, вызывать обработчики."""
        while self._running:
            try:
                new_requests = self._fetch_new_requests()
                for req in new_requests:
                    logger.info("New request #%d: %s -> %s: '%s'", 
                               req["id"], req["player_name"], req["npc_name"], req["message"])
                    for cb in self._callbacks:
                        try:
                            cb(req)
                        except Exception as e:
                            logger.error("Callback error: %s", e)
            except Exception as e:
                logger.error("Poll loop error: %s", e)
            
            time.sleep(0.5)  # 500ms между проверками
    
    def _fetch_new_requests(self) -> List[dict]:
        """Прочитать новые необработанные запросы из ai_requests."""
        requests = []
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                # FIX: LIMIT 50 вместо 10 — не теряем запросы при спаме
                sql = """
                    SELECT id, player_guid, player_name, npc_guid, npc_entry, 
                           npc_name, message, channel_type, target_is_player, created_at
                    FROM ai_requests
                    WHERE id > %s AND processed = 0
                    ORDER BY id ASC
                    LIMIT 50
                """
                cur.execute(sql, (self._last_request_id,))
                rows = cur.fetchall()
                
                for row in rows:
                    req_id, p_guid, p_name, n_guid, n_entry, n_name, msg, ch_type, is_player, created = row
                    
                    requests.append({
                        "id": req_id,
                        "player_guid": p_guid,
                        "player_name": p_name,
                        "npc_guid": n_guid,
                        "npc_entry": n_entry,
                        "npc_name": n_name,
                        "message": msg,
                        "channel_type": ch_type or "SAY",
                        "target_is_player": bool(is_player),
                        "created_at": created,
                    })
                    
                    # Обновляем последний обработанный ID
                    if req_id > self._last_request_id:
                        self._last_request_id = req_id
                
                # Помечаем как обработанные
                if rows:
                    ids = [r[0] for r in rows]
                    placeholders = ",".join(["%s"] * len(ids))
                    cur.execute(f"UPDATE ai_requests SET processed = 1 WHERE id IN ({placeholders})", ids)
                    logger.debug("Marked %d requests as processed", len(ids))
                    
        finally:
            conn.close()
        
        return requests
    
    def write_response(self, player_guid: int, npc_guid: int, npc_entry: int,
                       response_text: str, emote_id: int = 0, 
                       action_command: str = None, mood_change: str = None):
        """
        Записать ответ ИИ в ai_responses (Lua прочитает и заставит NPC/бота говорить).
        """
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                sql = """
                    INSERT INTO ai_responses 
                    (player_guid, npc_guid, npc_entry, response_text, emote_id, 
                     action_command, mood_change, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, UNIX_TIMESTAMP())
                """
                cur.execute(sql, (
                    player_guid, npc_guid, npc_entry,
                    response_text, emote_id,
                    action_command, mood_change
                ))
                logger.info("Response written: npc_guid=%d, text='%s...'", 
                           npc_guid, response_text[:50])
        except Exception as e:
            logger.error("Failed to write response: %s", e)
        finally:
            conn.close()
    
    def shutdown(self):
        """Остановить polling."""
        self._running = False
        logger.info("DB Bridge shutdown")
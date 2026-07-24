"""
WoWDBBridge — MySQL-based bridge between Python and WoW Eluna

ИСПРАВЛЕНИЕ v5.0:
- ai_requests/ai_responses → acore_characters (игровая база)
- Всё остальное → livingazeroth_ai (AI база)
- Добавлены bot_memory, bot_reputation
"""

import time
import threading
import logging
from typing import Callable, List, Optional, Dict, Any
from datetime import datetime

import pymysql

import config

logger = logging.getLogger(__name__)


class WoWDBBridge:
    """
    Читает ai_requests из MySQL, отправляет в обработчики,
    пишет ai_responses обратно в MySQL.
    Управляет npc_memory, npc_reputation, bot_memory, bot_reputation.
    """

    def __init__(self):
        self._running = True
        self._callbacks: List[Callable] = []
        self._last_request_id = 0
        
        # ─── Игровая база: ai_requests, ai_responses, characters ───
        self._game_db_config = {
            "host": config.MYSQL_HOST,
            "port": config.MYSQL_PORT,
            "user": config.MYSQL_USER,
            "password": config.MYSQL_PASSWORD,
            "database": config.MYSQL_DB_CHARACTERS,
            "charset": "utf8mb4",
            "autocommit": True,
        }
        
        # ─── AI база: memory, reputation, profiles, quests ───
        self._ai_db_config = {
            "host": config.MYSQL_HOST,
            "port": config.MYSQL_PORT,
            "user": config.MYSQL_USER,
            "password": config.MYSQL_PASSWORD,
            "database": config.MYSQL_DB_AI,
            "charset": "utf8mb4",
            "autocommit": True,
        }
        
        self._test_connections()
        self._init_last_id()
    
    def _get_game_conn(self):
        """Подключение к игровой базе (acore_characters)."""
        return pymysql.connect(**self._game_db_config)
    
    def _get_ai_conn(self):
        """Подключение к AI базе (livingazeroth_ai)."""
        return pymysql.connect(**self._ai_db_config)
    
    def _test_connections(self):
        try:
            conn = self._get_game_conn()
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            conn.close()
            logger.info("Game DB connected: %s/%s", config.MYSQL_HOST, config.MYSQL_DB_CHARACTERS)
        except Exception as e:
            logger.error("Game DB connection failed: %s", e)
            raise
        
        try:
            conn = self._get_ai_conn()
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            conn.close()
            logger.info("AI DB connected: %s/%s", config.MYSQL_HOST, config.MYSQL_DB_AI)
        except Exception as e:
            logger.error("AI DB connection failed: %s", e)
            raise

    def _init_last_id(self):
        # ai_requests в игровой базе
        conn = None
        try:
            conn = self._get_game_conn()
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(id) as max_id FROM ai_requests")
                row = cur.fetchone()
                self._last_request_id = row[0] or 0
                logger.info("Last request id set to %d", self._last_request_id)
        except Exception as e:
            logger.error("Failed to init last_request_id: %s", e)
            self._last_request_id = 0
        finally:
            if conn is not None:
                try:
                    conn.close()
                except pymysql.Error:
                    pass

    def start(self):
        threading.Thread(target=self._poll_loop, daemon=True, name="DBBridgePoller").start()
        logger.info("DB Bridge polling started")

    def register_callback(self, callback: Callable):
        self._callbacks.append(callback)
        logger.debug("Callback registered, total: %d", len(self._callbacks))

    def _poll_loop(self):
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
            
            time.sleep(0.5)
    
    def _fetch_new_requests(self) -> List[dict]:
        requests = []
        conn = self._get_game_conn()
        try:
            with conn.cursor() as cur:
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
                    
                    if req_id > self._last_request_id:
                        self._last_request_id = req_id

                if rows:
                    ids = [r[0] for r in rows]
                    placeholders = ",".join(["%s"] * len(ids))
                    cur.execute(f"UPDATE ai_requests SET processed = 1 WHERE id IN ({placeholders})", ids)
                    logger.debug("Marked %d requests as processed", len(ids))

        except Exception as e:
            logger.error("Fetch requests error: %s", e)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except pymysql.Error:
                    pass

        return requests
    
    def write_response(self, player_guid: int, npc_guid: int, npc_entry: int,
                       response_text: str, emote_id: int = 0,
                       action_command: str = None, mood_change: str = None):
        # ai_responses в игровой базе (Lua читает отсюда)
        conn = self._get_game_conn()
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
            if conn is not None:
                try:
                    conn.close()
                except pymysql.Error:
                    pass
    
    # ═══════════════════════════════════════════════════════════════
    # CHARACTER INFO (игровая база)
    # ═══════════════════════════════════════════════════════════════
    
    def get_character_info(self, guid: int) -> Optional[dict]:
        conn = self._get_game_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT name, race, class, level, gender FROM characters WHERE guid = %s",
                    (guid,)
                )
                row = cur.fetchone()
                if not row:
                    return None
                
                race_map = {
                    1: "Human", 2: "Orc", 3: "Dwarf", 4: "Night Elf",
                    5: "Undead", 6: "Tauren", 7: "Gnome", 8: "Troll",
                    10: "Blood Elf", 11: "Draenei"
                }
                class_map = {
                    1: "Warrior", 2: "Paladin", 3: "Hunter", 4: "Rogue",
                    5: "Priest", 6: "Death Knight", 7: "Shaman", 8: "Mage",
                    9: "Warlock", 11: "Druid"
                }
                
                # ═══════════════════════════════════════════════════════════
                # НОВОЕ v5.4: gender и gender_ru
                # ═══════════════════════════════════════════════════════════
                gender_raw = row[4] if len(row) > 4 else 0
                gender = "Male" if gender_raw == 0 else "Female"
                gender_ru = "мужчина" if gender_raw == 0 else "женщина"
                
                return {
                    "name": row[0],
                    "race": race_map.get(row[1], "Unknown"),
                    "class": class_map.get(row[2], "Unknown"),
                    "level": row[3],
                    "gender": gender,
                    "gender_ru": gender_ru,
                }
        except Exception as e:
            logger.error("Failed to get character info for guid %d: %s", guid, e)
            return None
        finally:
            if conn is not None:
                try:
                    conn.close()
                except pymysql.Error:
                    pass

    # ═══════════════════════════════════════════════════════════════
    # NPC MEMORY (AI база — livingazeroth_ai)
    # ═══════════════════════════════════════════════════════════════
    
    def save_npc_memory(self, npc_guid: int, npc_entry: int, player_guid: int,
                        player_name: str, player_message: str, npc_response: str,
                        mood_after: str = "нейтральный", reputation_after: int = 0) -> bool:
        """Сохранить запись диалога в npc_memory (livingazeroth_ai)."""
        conn = self._get_ai_conn()
        try:
            with conn.cursor() as cur:
                sql = """
                    INSERT INTO npc_memory 
                    (npc_guid, npc_entry, player_guid, player_name, memory_type,
                     content, player_message, npc_response, mood_after, reputation_after, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, UNIX_TIMESTAMP())
                """
                content = f"{player_name}: {player_message} → NPC: {npc_response}"
                cur.execute(sql, (
                    npc_guid, npc_entry, player_guid, player_name,
                    "dialogue", content, player_message, npc_response,
                    mood_after, reputation_after
                ))
                logger.debug("Memory saved for NPC %d, player %d", npc_guid, player_guid)
                return True
        except Exception as e:
            logger.error("Failed to save npc_memory: %s", e)
            return False
        finally:
            if conn is not None:
                try:
                    conn.close()
                except pymysql.Error:
                    pass
    
    def get_npc_memory(self, npc_guid: int, player_guid: int, limit: int = 10) -> list:
        """Получить историю диалогов NPC с игроком из livingazeroth_ai."""
        conn = self._get_ai_conn()
        try:
            with conn.cursor() as cur:
                sql = """
                    SELECT player_message, npc_response, mood_after, created_at
                    FROM npc_memory
                    WHERE npc_guid = %s AND player_guid = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                """
                cur.execute(sql, (npc_guid, player_guid, limit))
                rows = cur.fetchall()
                result = []
                for row in reversed(rows):
                    ts = row[3]
                    time_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else "unknown"
                    result.append({
                        "player_guid": player_guid,
                        "player_msg": row[0] or "",
                        "ai_reply": row[1] or "",
                        "mood": row[2] or "нейтральный",
                        "timestamp": time_str,
                    })
                return result
        except Exception as e:
            logger.error("Failed to get npc_memory: %s", e)
            return []
        finally:
            if conn is not None:
                try:
                    conn.close()
                except pymysql.Error:
                    pass
    
    # ═══════════════════════════════════════════════════════════════
    # NPC REPUTATION (AI база — livingazeroth_ai)
    # ═══════════════════════════════════════════════════════════════
    
    def update_npc_reputation(self, npc_guid: int, npc_entry: int, player_guid: int,
                              player_name: str, reputation_change: int = 0) -> int:
        """Обновить репутацию игрока у NPC. Возвращает новую репутацию."""
        conn = self._get_ai_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT reputation, total_dialogues FROM npc_reputation WHERE npc_guid = %s AND player_guid = %s",
                    (npc_guid, player_guid)
                )
                row = cur.fetchone()
                
                if row:
                    new_rep = row[0] + reputation_change
                    new_dialogues = row[1] + 1
                    rank = self._rep_to_rank(new_rep)
                    cur.execute(
                        """UPDATE npc_reputation 
                           SET reputation = %s, total_dialogues = %s, reputation_rank = %s,
                               last_interaction_at = UNIX_TIMESTAMP(), player_name = %s
                           WHERE npc_guid = %s AND player_guid = %s""",
                        (new_rep, new_dialogues, rank, player_name, npc_guid, player_guid)
                    )
                else:
                    new_rep = reputation_change
                    rank = self._rep_to_rank(new_rep)
                    cur.execute(
                        """INSERT INTO npc_reputation 
                           (npc_guid, npc_entry, player_guid, player_name, reputation,
                            reputation_rank, total_dialogues, last_interaction_at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, UNIX_TIMESTAMP())""",
                        (npc_guid, npc_entry, player_guid, player_name, new_rep, rank, 1)
                    )
                
                return new_rep
        except Exception as e:
            logger.error("Failed to update reputation: %s", e)
            return 0
        finally:
            if conn is not None:
                try:
                    conn.close()
                except pymysql.Error:
                    pass
    
    def get_npc_reputation(self, npc_guid: int, player_guid: int) -> int:
        """Получить текущую репутацию игрока у NPC."""
        conn = self._get_ai_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT reputation FROM npc_reputation WHERE npc_guid = %s AND player_guid = %s",
                    (npc_guid, player_guid)
                )
                row = cur.fetchone()
                return row[0] if row else 0
        except Exception as e:
            logger.error("Failed to get npc_reputation: %s", e)
            return 0
        finally:
            if conn is not None:
                try:
                    conn.close()
                except pymysql.Error:
                    pass
    
    # ═══════════════════════════════════════════════════════════════
    # BOT MEMORY (AI база — livingazeroth_ai) — НОВОЕ
    # ═══════════════════════════════════════════════════════════════
    
    def save_bot_memory(self, bot_guid: int, player_guid: int,
                       player_name: str, player_message: str, bot_response: str,
                       mood_after: str = "нейтральный", reputation_after: int = 0,
                       location: str = None) -> bool:
        """Сохранить запись диалога с ботом в bot_memory."""
        conn = self._get_ai_conn()
        try:
            with conn.cursor() as cur:
                sql = """
                    INSERT INTO bot_memory 
                    (bot_guid, player_guid, player_name, memory_type,
                     content, player_message, bot_response, mood_after, reputation_after, location, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, UNIX_TIMESTAMP())
                """
                content = f"{player_name}: {player_message} → Bot: {bot_response}"
                cur.execute(sql, (
                    bot_guid, player_guid, player_name,
                    "dialogue", content, player_message, bot_response,
                    mood_after, reputation_after, location
                ))
                logger.debug("Memory saved for bot %d, player %d", bot_guid, player_guid)
                return True
        except Exception as e:
            logger.error("Failed to save bot_memory: %s", e)
            return False
        finally:
            if conn is not None:
                try:
                    conn.close()
                except pymysql.Error:
                    pass
    
    def get_bot_memory(self, bot_guid: int, player_guid: int, limit: int = 10) -> list:
        """Получить историю диалогов бота с игроком."""
        conn = self._get_ai_conn()
        try:
            with conn.cursor() as cur:
                sql = """
                    SELECT player_message, bot_response, mood_after, created_at
                    FROM bot_memory
                    WHERE bot_guid = %s AND player_guid = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                """
                cur.execute(sql, (bot_guid, player_guid, limit))
                rows = cur.fetchall()
                result = []
                for row in reversed(rows):
                    ts = row[3]
                    time_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else "unknown"
                    result.append({
                        "player_guid": player_guid,
                        "player_msg": row[0] or "",
                        "ai_reply": row[1] or "",
                        "mood": row[2] or "нейтральный",
                        "timestamp": time_str,
                    })
                return result
        except Exception as e:
            logger.error("Failed to get bot_memory: %s", e)
            return []
        finally:
            if conn is not None:
                try:
                    conn.close()
                except pymysql.Error:
                    pass
    
    # ═══════════════════════════════════════════════════════════════
    # BOT REPUTATION (AI база — livingazeroth_ai) — НОВОЕ
    # ═══════════════════════════════════════════════════════════════
    
    def update_bot_reputation(self, bot_guid: int, player_guid: int,
                              player_name: str, reputation_change: int = 0) -> int:
        """Обновить репутацию игрока у бота. Возвращает новую репутацию."""
        conn = self._get_ai_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT reputation, total_dialogues FROM bot_reputation WHERE bot_guid = %s AND player_guid = %s",
                    (bot_guid, player_guid)
                )
                row = cur.fetchone()
                
                if row:
                    new_rep = row[0] + reputation_change
                    new_dialogues = row[1] + 1
                    rank = self._rep_to_rank(new_rep)
                    cur.execute(
                        """UPDATE bot_reputation 
                           SET reputation = %s, total_dialogues = %s, reputation_rank = %s,
                               last_interaction_at = UNIX_TIMESTAMP(), player_name = %s
                           WHERE bot_guid = %s AND player_guid = %s""",
                        (new_rep, new_dialogues, rank, player_name, bot_guid, player_guid)
                    )
                else:
                    new_rep = reputation_change
                    rank = self._rep_to_rank(new_rep)
                    cur.execute(
                        """INSERT INTO bot_reputation 
                           (bot_guid, player_guid, player_name, reputation,
                            reputation_rank, total_dialogues, last_interaction_at)
                           VALUES (%s, %s, %s, %s, %s, %s, UNIX_TIMESTAMP())""",
                        (bot_guid, player_guid, player_name, new_rep, rank, 1)
                    )
                
                return new_rep
        except Exception as e:
            logger.error("Failed to update bot_reputation: %s", e)
            return 0
        finally:
            if conn is not None:
                try:
                    conn.close()
                except pymysql.Error:
                    pass
    
    def get_bot_reputation(self, bot_guid: int, player_guid: int) -> int:
        """Получить текущую репутацию игрока у бота."""
        conn = self._get_ai_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT reputation FROM bot_reputation WHERE bot_guid = %s AND player_guid = %s",
                    (bot_guid, player_guid)
                )
                row = cur.fetchone()
                return row[0] if row else 0
        except Exception as e:
            logger.error("Failed to get bot_reputation: %s", e)
            return 0
        finally:
            if conn is not None:
                try:
                    conn.close()
                except pymysql.Error:
                    pass
    
    @staticmethod
    def _rep_to_rank(rep: int) -> str:
        if rep < -50:
            return "hostile"
        elif rep < 0:
            return "unfriendly"
        elif rep < 50:
            return "neutral"
        elif rep < 100:
            return "friendly"
        else:
            return "honored"
    
    def shutdown(self):
        self._running = False
        logger.info("DB Bridge shutdown")
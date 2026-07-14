"""
WoWDBBridge v3.0 — Две базы: игровая + AI
Игровая: ai_requests, ai_responses
Наша: npc_memory, npc_reputation, npc_quests, player_quest_progress, npc_profiles
"""

import time
import threading
import logging
from typing import Callable, List, Optional, Dict, Any

import pymysql

import config

logger = logging.getLogger(__name__)


class WoWDBBridge:
    """
    Мост между Python и AzerothCore через MySQL.
    Две независимые коннекции.
    """

    def __init__(self):
        self._running = True
        self._callbacks: List[Callable] = []
        self._last_request_id = 0

        # Конфиг игровой базы (acore_characters)
        self._db_game_config = {
            "host": config.MYSQL_HOST,
            "port": config.MYSQL_PORT,
            "user": config.MYSQL_USER,
            "password": config.MYSQL_PASSWORD,
            "database": config.MYSQL_DB_CHARACTERS,
            "charset": "utf8mb4",
            "autocommit": True,
        }

        # Конфиг нашей базы (livingazeroth_ai)
        self._db_ai_config = {
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
        """Соединение с игровой базой (только ai_requests/ai_responses)."""
        return pymysql.connect(**self._db_game_config)

    def _get_ai_conn(self):
        """Соединение с нашей базой (память, репутация, квесты, профили)."""
        return pymysql.connect(**self._db_ai_config)

    def _test_connections(self):
        try:
            # Проверяем игровую
            conn = self._get_game_conn()
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            conn.close()
            logger.info("Game DB connected: %s/%s", config.MYSQL_HOST, config.MYSQL_DB_CHARACTERS)

            # Проверяем нашу
            conn = self._get_ai_conn()
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            conn.close()
            logger.info("AI DB connected: %s/%s", config.MYSQL_HOST, config.MYSQL_DB_AI)

        except Exception as e:
            logger.error("MySQL connection failed: %s", e)
            raise

    def _init_last_id(self):
        """При старте смотрим MAX(id) в ai_requests."""
        try:
            conn = self._get_game_conn()
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
        """Запустить фоновый polling ai_requests."""
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
        """Читаем ai_requests из игровой базы."""
        requests = []
        conn = None
        try:
            conn = self._get_game_conn()
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

        finally:
            if conn is not None:
                conn.close()

        return requests

    # ═══════════════════════════════════════════════════════════════
    # ai_responses — пишем в игровую базу (Lua читает оттуда)
    # ═══════════════════════════════════════════════════════════════

    def write_response(self, player_guid: int, npc_guid: int, npc_entry: int,
                       response_text: str, emote_id: int = 0,
                       action_command: str = None, mood_change: str = None):
        conn = None
        try:
            conn = self._get_game_conn()
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
                logger.info("Response written: npc=%d, text='%s...'",
                           npc_guid, response_text[:50])
        except Exception as e:
            logger.error("Failed to write response: %s", e)
        finally:
            if conn is not None:
                conn.close()

    # ═══════════════════════════════════════════════════════════════
    # npc_memory — наша база (livingazeroth_ai)
    # ═══════════════════════════════════════════════════════════════

    def save_memory(self, npc_guid: int, npc_entry: int, player_guid: int,
                    player_name: str, memory_type: str, content: str,
                    player_message: str = None, npc_response: str = None,
                    mood_after: str = None, reputation_after: int = 0,
                    location: str = None) -> bool:
        conn = None
        try:
            conn = self._get_ai_conn()
            with conn.cursor() as cur:
                sql = """
                    INSERT INTO npc_memory
                    (npc_guid, npc_entry, player_guid, player_name, memory_type,
                     content, player_message, npc_response, mood_after, reputation_after,
                     location, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, UNIX_TIMESTAMP())
                """
                cur.execute(sql, (
                    npc_guid, npc_entry, player_guid, player_name, memory_type,
                    content, player_message, npc_response, mood_after, reputation_after,
                    location
                ))
                logger.debug("Memory saved: npc=%d, player=%s", npc_guid, player_name)
                return True
        except Exception as e:
            logger.error("Failed to save memory: %s", e)
            return False
        finally:
            if conn is not None:
                conn.close()

    def get_memory(self, npc_guid: int, player_guid: int = None,
                   limit: int = 10) -> List[Dict]:
        conn = None
        try:
            conn = self._get_ai_conn()
            with conn.cursor() as cur:
                if player_guid:
                    sql = """
                        SELECT memory_type, content, player_message, npc_response,
                               mood_after, reputation_after, created_at
                        FROM npc_memory
                        WHERE npc_guid = %s AND player_guid = %s
                        ORDER BY created_at DESC
                        LIMIT %s
                    """
                    cur.execute(sql, (npc_guid, player_guid, limit))
                else:
                    sql = """
                        SELECT player_name, memory_type, content, created_at
                        FROM npc_memory
                        WHERE npc_guid = %s
                        ORDER BY created_at DESC
                        LIMIT %s
                    """
                    cur.execute(sql, (npc_guid, limit))

                rows = cur.fetchall()
                result = []
                for row in rows:
                    if player_guid:
                        result.append({
                            "memory_type": row[0],
                            "content": row[1],
                            "player_message": row[2],
                            "npc_response": row[3],
                            "mood_after": row[4],
                            "reputation_after": row[5],
                            "created_at": row[6],
                        })
                    else:
                        result.append({
                            "player_name": row[0],
                            "memory_type": row[1],
                            "content": row[2],
                            "created_at": row[3],
                        })
                return result
        except Exception as e:
            logger.error("Failed to get memory: %s", e)
            return []
        finally:
            if conn is not None:
                conn.close()

    # ═══════════════════════════════════════════════════════════════
    # npc_reputation — наша база
    # ═══════════════════════════════════════════════════════════════

    def get_reputation(self, npc_guid: int, player_guid: int) -> Dict:
        conn = None
        try:
            conn = self._get_ai_conn()
            with conn.cursor() as cur:
                sql = """
                    SELECT reputation, reputation_rank, total_dialogues,
                           quests_given, quests_completed, last_interaction_at
                    FROM npc_reputation
                    WHERE npc_guid = %s AND player_guid = %s
                """
                cur.execute(sql, (npc_guid, player_guid))
                row = cur.fetchone()

                if row:
                    return {
                        "reputation": row[0],
                        "rank": row[1],
                        "total_dialogues": row[2],
                        "quests_given": row[3],
                        "quests_completed": row[4],
                        "last_interaction_at": row[5],
                    }
                else:
                    return {
                        "reputation": 0,
                        "rank": "neutral",
                        "total_dialogues": 0,
                        "quests_given": 0,
                        "quests_completed": 0,
                        "last_interaction_at": 0,
                    }
        except Exception as e:
            logger.error("Failed to get reputation: %s", e)
            return {"reputation": 0, "rank": "neutral"}
        finally:
            if conn is not None:
                conn.close()

    def update_reputation(self, npc_guid: int, npc_entry: int, player_guid: int,
                          player_name: str, delta: int,
                          dialogue_increment: bool = False) -> int:
        conn = None
        try:
            conn = self._get_ai_conn()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT reputation, total_dialogues FROM npc_reputation WHERE npc_guid = %s AND player_guid = %s",
                    (npc_guid, player_guid)
                )
                row = cur.fetchone()

                if row:
                    current_rep = row[0]
                    current_dialogues = row[1]
                    new_rep = max(-100, min(100, current_rep + delta))
                    new_dialogues = current_dialogues + (1 if dialogue_increment else 0)
                    rank = self._rep_to_rank(new_rep)

                    cur.execute("""
                        UPDATE npc_reputation
                        SET reputation = %s, reputation_rank = %s, total_dialogues = %s,
                            last_interaction_at = UNIX_TIMESTAMP()
                        WHERE npc_guid = %s AND player_guid = %s
                    """, (new_rep, rank, new_dialogues, npc_guid, player_guid))
                else:
                    new_rep = max(-100, min(100, delta))
                    rank = self._rep_to_rank(new_rep)
                    dialogues = 1 if dialogue_increment else 0

                    cur.execute("""
                        INSERT INTO npc_reputation
                        (npc_guid, npc_entry, player_guid, player_name, reputation,
                         reputation_rank, total_dialogues, last_interaction_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, UNIX_TIMESTAMP())
                    """, (npc_guid, npc_entry, player_guid, player_name, new_rep,
                          rank, dialogues))

                return new_rep
        except Exception as e:
            logger.error("Failed to update reputation: %s", e)
            return 0
        finally:
            if conn is not None:
                conn.close()

    @staticmethod
    def _rep_to_rank(rep: int) -> str:
        if rep < -50: return "hostile"
        elif rep < 0: return "unfriendly"
        elif rep < 50: return "neutral"
        elif rep < 100: return "friendly"
        else: return "honored"

    # ═══════════════════════════════════════════════════════════════
    # КВЕСТЫ — наша база
    # ═══════════════════════════════════════════════════════════════

    def get_quest(self, quest_id: str) -> Optional[Dict]:
        conn = None
        try:
            conn = self._get_ai_conn()
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM npc_quests WHERE quest_id = %s", (quest_id,))
                row = cur.fetchone()
                if row:
                    return {
                        "quest_id": row[1],
                        "quest_name": row[2],
                        "quest_description": row[3],
                        "giver_npc_entry": row[4],
                        "required_item_entry": row[6],
                        "required_item_count": row[7],
                        "reward_gold": row[10],
                        "reward_reputation": row[13],
                    }
                return None
        except Exception as e:
            logger.error("Failed to get quest: %s", e)
            return None
        finally:
            if conn is not None:
                conn.close()

    def get_player_quest_status(self, player_guid: int, quest_id: str) -> Optional[Dict]:
        conn = None
        try:
            conn = self._get_ai_conn()
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT status, item_count, npc_kills, given_at, completed_at
                    FROM player_quest_progress
                    WHERE player_guid = %s AND quest_id = %s
                """, (player_guid, quest_id))
                row = cur.fetchone()
                if row:
                    return {
                        "status": row[0],
                        "item_count": row[1],
                        "npc_kills": row[2],
                        "given_at": row[3],
                        "completed_at": row[4],
                    }
                return None
        except Exception as e:
            logger.error("Failed to get quest status: %s", e)
            return None
        finally:
            if conn is not None:
                conn.close()

    def give_quest(self, player_guid: int, player_name: str,
                   quest_id: str, giver_npc_guid: int) -> bool:
        conn = None
        try:
            conn = self._get_ai_conn()
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO player_quest_progress
                    (player_guid, player_name, quest_id, status, given_by_npc_guid, given_at)
                    VALUES (%s, %s, %s, 'active', %s, UNIX_TIMESTAMP())
                    ON DUPLICATE KEY UPDATE status = 'active', given_at = UNIX_TIMESTAMP()
                """, (player_guid, player_name, quest_id, giver_npc_guid))
                logger.info("Quest %s given to player %s", quest_id, player_name)
                return True
        except Exception as e:
            logger.error("Failed to give quest: %s", e)
            return False
        finally:
            if conn is not None:
                conn.close()

    def update_quest_progress(self, player_guid: int, quest_id: str,
                              item_delta: int = 0, kill_delta: int = 0) -> Dict:
        conn = None
        try:
            conn = self._get_ai_conn()
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT item_count, npc_kills FROM player_quest_progress
                    WHERE player_guid = %s AND quest_id = %s AND status = 'active'
                """, (player_guid, quest_id))
                row = cur.fetchone()

                if not row:
                    return {"status": "not_found"}

                new_items = row[0] + item_delta
                new_kills = row[1] + kill_delta

                cur.execute("""
                    UPDATE player_quest_progress
                    SET item_count = %s, npc_kills = %s
                    WHERE player_guid = %s AND quest_id = %s
                """, (new_items, new_kills, player_guid, quest_id))

                return {"status": "active", "item_count": new_items, "npc_kills": new_kills}
        except Exception as e:
            logger.error("Failed to update quest progress: %s", e)
            return {"status": "error"}
        finally:
            if conn is not None:
                conn.close()

    def complete_quest(self, player_guid: int, quest_id: str) -> Dict:
        conn = None
        try:
            conn = self._get_ai_conn()
            with conn.cursor() as cur:
                cur.execute("SELECT reward_gold, reward_reputation FROM npc_quests WHERE quest_id = %s", (quest_id,))
                quest_row = cur.fetchone()

                if not quest_row:
                    return {"success": False, "error": "Quest not found"}

                reward_gold = quest_row[0]
                reward_rep = quest_row[1]

                cur.execute("""
                    UPDATE player_quest_progress
                    SET status = 'completed', completed_at = UNIX_TIMESTAMP()
                    WHERE player_guid = %s AND quest_id = %s
                """, (player_guid, quest_id))

                return {
                    "success": True,
                    "reward_gold": reward_gold,
                    "reward_reputation": reward_rep,
                }
        except Exception as e:
            logger.error("Failed to complete quest: %s", e)
            return {"success": False, "error": str(e)}
        finally:
            if conn is not None:
                conn.close()

    # ═══════════════════════════════════════════════════════════════
    # ПРОФИЛИ NPC — наша база
    # ═══════════════════════════════════════════════════════════════

    def get_npc_profile(self, npc_entry: int, npc_guid: int) -> Optional[Dict]:
        """Получить профиль NPC из нашей базы (Уровень 2)."""
        conn = None
        try:
            conn = self._get_ai_conn()
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT npc_name, role, trait, faction, home_location,
                           speech_style, can_give_quests, profile_data,
                           generated_by, usage_count
                    FROM npc_profiles
                    WHERE npc_entry = %s AND npc_guid = %s
                """, (npc_entry, npc_guid))
                row = cur.fetchone()

                if not row:
                    return None

                import json
                profile_data = {}
                if row[7]:
                    try:
                        profile_data = json.loads(row[7])
                    except json.JSONDecodeError:
                        pass

                return {
                    "name": row[0],
                    "role": row[1],
                    "trait": row[2],
                    "faction": row[3],
                    "home_location": row[4],
                    "speech_style": row[5],
                    "can_give_quests": bool(row[6]),
                    "knowledge": profile_data.get("knowledge", []),
                    "mood_default": profile_data.get("mood_default", "нейтральный"),
                    "quests": profile_data.get("quests", []),
                    "generated_by": row[8],
                    "usage_count": row[9],
                }
        except Exception as e:
            logger.error("Failed to get NPC profile: %s", e)
            return None
        finally:
            if conn is not None:
                conn.close()

    def save_npc_profile(self, npc_entry: int, npc_guid: int, npc_name: str,
                         profile: Dict, generated_by: str = "llm",
                         generation_prompt: str = None) -> bool:
        """Сохранить профиль NPC в нашу базу."""
        conn = None
        try:
            import json
            profile_data = {
                "knowledge": profile.get("knowledge", []),
                "mood_default": profile.get("mood_default", "нейтральный"),
                "quests": profile.get("quests", []),
                "gossip_text": profile.get("gossip_text", ""),
            }

            conn = self._get_ai_conn()
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO npc_profiles
                    (npc_entry, npc_guid, npc_name, role, trait, faction,
                     home_location, speech_style, can_give_quests,
                     profile_data, generated_by, generation_prompt,
                     created_at, updated_at, usage_count)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, UNIX_TIMESTAMP(), UNIX_TIMESTAMP(), 1)
                    ON DUPLICATE KEY UPDATE
                        npc_name = VALUES(npc_name),
                        role = VALUES(role),
                        trait = VALUES(trait),
                        faction = VALUES(faction),
                        home_location = VALUES(home_location),
                        speech_style = VALUES(speech_style),
                        can_give_quests = VALUES(can_give_quests),
                        profile_data = VALUES(profile_data),
                        generated_by = VALUES(generated_by),
                        generation_prompt = VALUES(generation_prompt),
                        updated_at = UNIX_TIMESTAMP(),
                        usage_count = usage_count + 1
                """, (
                    npc_entry, npc_guid, npc_name,
                    profile.get("role", "Житель"),
                    profile.get("trait", "Обычный"),
                    profile.get("faction", "Нейтральная"),
                    profile.get("home_location", "Неизвестно"),
                    profile.get("speech_style", "Обычный"),
                    1 if profile.get("can_give_quests", False) else 0,
                    json.dumps(profile_data, ensure_ascii=False),
                    generated_by,
                    generation_prompt,
                ))
                logger.info("Profile saved: entry=%d, guid=%d, name=%s", npc_entry, npc_guid, npc_name)
                return True
        except Exception as e:
            logger.error("Failed to save NPC profile: %s", e)
            return False
        finally:
            if conn is not None:
                conn.close()

    def log_generated_profile(self, npc_entry: int, npc_guid: int, npc_name: str,
                              prompt: str, raw_response: str, parsed_profile: Dict) -> bool:
        """Лог генерации профиля для отладки."""
        conn = None
        try:
            import json
            conn = self._get_ai_conn()
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO generated_profiles
                    (npc_entry, npc_guid, npc_name, generation_prompt, raw_llm_response, parsed_profile, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, UNIX_TIMESTAMP())
                """, (npc_entry, npc_guid, npc_name, prompt, raw_response,
                      json.dumps(parsed_profile, ensure_ascii=False)))
                return True
        except Exception as e:
            logger.error("Failed to log generated profile: %s", e)
            return False
        finally:
            if conn is not None:
                conn.close()

    def shutdown(self):
        self._running = False
        logger.info("DB Bridge shutdown")
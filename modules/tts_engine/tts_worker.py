"""
TTS Worker — XTTS v2 (клонирование голоса, русский)
Запускается ОТДЕЛЬНО от main.py: python -m modules.tts_engine.tts_worker
"""

import logging
import time
import threading
from pathlib import Path
from typing import Optional

import requests
import pymysql

import config
from modules.tts_engine.voice_map import get_voice_path, get_ref_text

logger = logging.getLogger("tts_worker")

if not getattr(config, 'TTS_ENABLED', False):
    logger.info("TTS is disabled in config. Exiting.")
    exit(0)


class TTSWorker:
    def __init__(self):
        self._running = True
        self._last_id = 0
        self._db_config = {
            "host": config.MYSQL_HOST,
            "port": config.MYSQL_PORT,
            "user": config.MYSQL_USER,
            "password": config.MYSQL_PASSWORD,
            "database": config.MYSQL_DB_CHARACTERS,
            "charset": "utf8mb4",
            "autocommit": True,
        }
        self._init_last_id()
    
    def _get_conn(self):
        return pymysql.connect(**self._db_config)
    
    def _init_last_id(self):
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(id) FROM ai_responses WHERE tts_played = 1")
                row = cur.fetchone()
                self._last_id = row[0] or 0
        except Exception as e:
            logger.error("Failed to init last_id: %s", e)
            self._last_id = 0
        finally:
            conn.close()
    
    def _fetch_new_responses(self):
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                sql = """
                    SELECT r.id, r.player_guid, r.npc_guid, r.npc_entry, 
                           r.response_text, r.emote_id
                    FROM ai_responses r
                    WHERE r.id > %s AND r.fetched = 1 AND (r.tts_played = 0 OR r.tts_played IS NULL)
                    ORDER BY r.id ASC
                    LIMIT 10
                """
                cur.execute(sql, (self._last_id,))
                rows = cur.fetchall()
                result = []
                for row in rows:
                    result.append({
                        "id": row[0],
                        "player_guid": row[1],
                        "npc_guid": row[2],
                        "npc_entry": row[3],
                        "text": row[4],
                        "emote_id": row[5],                        
                    })
                    if row[0] > self._last_id:
                        self._last_id = row[0]
                return result
        except Exception as e:
            logger.error("Fetch error: %s", e)
            return []
        finally:
            conn.close()
    
    def _get_bot_info(self, bot_guid: int) -> Optional[dict]:
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT race, gender FROM characters WHERE guid = %s",
                    (bot_guid,)
                )
                row = cur.fetchone()
                if not row:
                    return None
                race_map = {
                    1: "Human", 2: "Orc", 3: "Dwarf", 4: "Night Elf",
                    5: "Undead", 6: "Tauren", 7: "Gnome", 8: "Troll",
                    10: "Blood Elf", 11: "Draenei"
                }
                return {
                    "race": race_map.get(row[0], "Human"),
                    "gender": "Male" if row[1] == 0 else "Female",
                }
        except Exception as e:
            logger.error("Failed to get bot info: %s", e)
            return None
        finally:
            conn.close()
    
    def _play_tts(self, text: str, race: str, gender: str):
        """Сгенерировать и воспроизвести TTS через XTTS v2."""
        try:
            ref_path = get_voice_path(race, gender)
            if not ref_path.exists():
                logger.error("Ref audio not found: %s", ref_path)
                return

            ref_text = get_ref_text(race, gender)
            if not ref_text:
                logger.error("Ref text empty for %s %s", race, gender)
                return

            # XTTS лучше с короткими фразами
            if len(text) > 150:
                text = text[:147] + "..."

            payload = {
                "text": text,
                "ref_audio": str(ref_path.resolve()),
                "ref_text": ref_text,
            }
            
            resp = requests.post(
                f"{config.TTS_API_URL}/inference",
                json=payload,
                timeout=60
            )

            if resp.status_code == 200:
                self._play_audio(resp.content)
                logger.info("XTTS v2 played for %s %s: '%s...'", race, gender, text[:40])
            else:
                logger.error("XTTS v2 returned %d: %s", resp.status_code, resp.text[:200])

        except Exception as e:
            logger.error("TTS failed: %s", e)

    def _play_audio(self, audio_data: bytes):
        """Воспроизвести WAV-данные через локальные колонки."""
        try:
            import sounddevice as sd
            import soundfile as sf
            import io
            
            with io.BytesIO(audio_data) as f:
                data, samplerate = sf.read(f)
                sd.play(data, samplerate)
                sd.wait()
        except ImportError:
            out_path = config.LOGS_DIR / "last_tts.wav"
            with open(out_path, "wb") as f:
                f.write(audio_data)
            logger.info("Audio saved to %s (install sounddevice+soundfile for playback)", out_path)

    def _mark_tts_played(self, response_id: int):
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ai_responses SET tts_played = 1 WHERE id = %s",
                    (response_id,)
                )
        except Exception as e:
            logger.error("Failed to mark tts_played: %s", e)
        finally:
            conn.close()
    
    def run(self):
        logger.info("TTS Worker started. Engine: xtts_v2")
        while self._running:
            try:
                responses = self._fetch_new_responses()
                for resp in responses:
                    info = self._get_bot_info(resp["npc_guid"])
                    if info:
                        self._play_tts(resp["text"], info["race"], info["gender"])
                    else:
                        self._play_tts(resp["text"], "Human", "Male")
                    
                    self._mark_tts_played(resp["id"])
                
                time.sleep(0.5)
            except Exception as e:
                logger.error("Worker loop error: %s", e)
                time.sleep(2)
    
    def shutdown(self):
        self._running = False
        logger.info("TTS Worker stopped")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    worker = TTSWorker()
    try:
        worker.run()
    except KeyboardInterrupt:
        worker.shutdown()
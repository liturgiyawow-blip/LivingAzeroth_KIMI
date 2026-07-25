"""
TTS Worker — асинхронный озвучиватель ботов
Запускается ОТДЕЛЬНО от main.py: python -m modules.tts_engine.tts_worker
Можно убить Ctrl+C, main.py продолжит работать.
"""

import logging
import time
import threading
from pathlib import Path
from typing import Optional

import requests
import pymysql

import config
from modules.tts_engine.voice_map import get_voice_path, get_voice_id

logger = logging.getLogger("tts_worker")

# Проверка: включена ли озвучка
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
        """Получить новые ответы, которые ещё не озвучены."""
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                # Нам нужен npc_guid, чтобы определить голос
                # Для ботов: npc_guid = bot_guid. Ищем в characters расу/пол.
                sql = """
                    SELECT r.id, r.player_guid, r.npc_guid, r.npc_entry, 
                           r.response_text, r.emote_id, r.channel_type
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
                        "channel": row[6] or "SAY",
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
        """Получить расу/пол бота из characters."""
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
        """Сгенерировать и воспроизвести TTS."""
        try:
            if config.TTS_ENGINE == "f5_tts":
                ref_path = get_voice_path(race, gender)
                if not ref_path.exists():
                    logger.warning("Ref audio not found: %s, using fallback", ref_path)
                    ref_path = get_voice_path("Human", "Male")
                
                # F5-TTS API (пример, endpoint зависит от реализации сервера)
                payload = {
                    "text": text,
                    "ref_audio": str(ref_path),
                    "ref_text": "",  # Можно оставить пустым для F5-TTS
                }
                resp = requests.post(f"{config.TTS_API_URL}/inference", json=payload, timeout=30)
                resp.raise_for_status()
                audio_data = resp.content
                
            elif config.TTS_ENGINE == "gpt_sovits":
                voice_id = get_voice_id(race, gender)
                payload = {
                    "text": text,
                    "model_name": voice_id,
                }
                resp = requests.post(f"{config.TTS_API_URL}/tts", json=payload, timeout=30)
                resp.raise_for_status()
                audio_data = resp.content
            
            else:
                logger.error("Unknown TTS engine: %s", config.TTS_ENGINE)
                return
            
            # Воспроизведение
            self._play_audio(audio_data)
            logger.info("TTS played for %s %s: '%s...'", race, gender, text[:40])
            
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
            # Fallback: сохранить файл
            out_path = config.LOGS_DIR / "last_tts.wav"
            with open(out_path, "wb") as f:
                f.write(audio_data)
            logger.info("Audio saved to %s (install sounddevice+soundfile for playback)", out_path)
    
    def _mark_tts_played(self, response_id: int):
        """Пометить, что озвучка выполнена."""
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
        logger.info("TTS Worker started. Engine: %s", config.TTS_ENGINE)
        while self._running:
            try:
                responses = self._fetch_new_responses()
                for resp in responses:
                    # Определяем голос по npc_guid (для ботов) или npc_entry (для NPC)
                    info = self._get_bot_info(resp["npc_guid"])
                    if info:
                        self._play_tts(resp["text"], info["race"], info["gender"])
                    else:
                        # Fallback для NPC (пока Human Male)
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
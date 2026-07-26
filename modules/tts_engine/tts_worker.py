"""
TTS Worker v2.1 — FIX: старые записи не блокируют новые

АРХИТЕКТУРА:
  Этап 1 (ГЕНЕРАЦИЯ): fetched=0 AND tts_played=0 AND created_at > NOW-5min
    → ORDER BY id DESC (новые первыми)
    → генерируем голос, сохраняем в RAM-кэш
    → ставим tts_played = 1

  Этап 2 (ВОСПРОИЗВЕДЕНИЕ): fetched=1 AND tts_played=1
    → проигрываем звук из RAM-кэша
    → ставим tts_played = 2
"""

import logging
import time
from pathlib import Path
from typing import Optional

import requests
import pymysql

import config
from modules.tts_engine.voice_map import get_voice_path, get_ref_text

logger = logging.getLogger("tts_worker")

if not getattr(config, "TTS_ENABLED", False):
    logger.info("TTS is disabled in config. Exiting.")
    exit(0)


class TTSWorker:
    def __init__(self):
        self._running = True
        self._db_config = {
            "host": config.MYSQL_HOST,
            "port": config.MYSQL_PORT,
            "user": config.MYSQL_USER,
            "password": config.MYSQL_PASSWORD,
            "database": config.MYSQL_DB_CHARACTERS,
            "charset": "utf8mb4",
            "autocommit": True,
        }
        # RAM-кэш сгенерированного аудио: {response_id: wav_bytes}
        self._audio_cache: dict[int, bytes] = {}

    def _get_conn(self):
        return pymysql.connect(**self._db_config)

    # ═══════════════════════════════════════════════════════════════════
    # ЭТАП 1: ГЕНЕРАЦИЯ ГОЛОСА
    # FIX v2.1: только свежие записи (5 мин), новые первыми (DESC)
    # ═══════════════════════════════════════════════════════════════════
    def _fetch_to_generate(self) -> list[dict]:
        """Найти ответы, для которых нужно сгенерировать голос."""
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                # FIX: created_at > UNIX_TIMESTAMP() - 300 (5 минут)
                # FIX: ORDER BY id DESC — новые записи в приоритете
                # FIX: LIMIT 20 — больше записей за раз
                sql = """
                    SELECT r.id, r.player_guid, r.npc_guid, r.npc_entry,
                           r.response_text, r.emote_id
                    FROM ai_responses r
                    WHERE r.fetched = 0
                      AND (r.tts_played = 0 OR r.tts_played IS NULL)
                      AND r.created_at > UNIX_TIMESTAMP() - 300
                    ORDER BY r.id DESC
                    LIMIT 20
                """
                cur.execute(sql)
                rows = cur.fetchall()
                result = [
                    {
                        "id": row[0],
                        "player_guid": row[1],
                        "npc_guid": row[2],
                        "npc_entry": row[3],
                        "text": row[4],
                        "emote_id": row[5],
                    }
                    for row in rows
                ]
                if result:
                    ids = [r["id"] for r in result]
                    logger.info("[GEN] Found %d responses to generate: ids=%s", len(result), ids)
                return result
        except Exception as e:
            logger.error("Fetch (generate) error: %s", e)
            return []
        finally:
            conn.close()

    def _generate_tts(self, text: str, race: str, gender: str) -> Optional[bytes]:
        """Сгенерировать TTS, вернуть WAV bytes. None при ошибке."""
        try:
            ref_path = get_voice_path(race, gender)
            if not ref_path.exists():
                logger.error("Ref audio not found: %s", ref_path)
                return None

            ref_text = get_ref_text(race, gender)
            if not ref_text:
                logger.error("Ref text empty for %s %s", race, gender)
                return None

            payload = {
                "text": text,
                "ref_audio": str(ref_path.resolve()),
                "ref_text": ref_text,
            }

            start = time.time()
            resp = requests.post(
                f"{config.TTS_API_URL}/inference",
                json=payload,
                timeout=120,
            )
            elapsed = time.time() - start

            if resp.status_code == 200:
                logger.info(
                    "[GEN] TTS done in %.1fs for %s %s (%d chars)",
                    elapsed, race, gender, len(text)
                )
                return resp.content
            else:
                logger.error("XTTS returned %d: %s", resp.status_code, resp.text[:200])
                return None

        except Exception as e:
            logger.error("TTS generation failed: %s", e)
            return None

    def _mark_tts_ready(self, response_id: int):
        """Поставить tts_played=1 (голос готов, Lua может показывать текст)."""
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ai_responses SET tts_played = 1 WHERE id = %s",
                    (response_id,),
                )
        except Exception as e:
            logger.error("Failed to mark tts_ready: %s", e)
        finally:
            conn.close()

    # ═══════════════════════════════════════════════════════════════════
    # ЭТАП 2: ВОСПРОИЗВЕДЕНИЕ
    # ═══════════════════════════════════════════════════════════════════
    def _fetch_to_play(self) -> list[dict]:
        """Найти ответы, текст которых уже показан, а звук ещё не проигран."""
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                sql = """
                    SELECT r.id
                    FROM ai_responses r
                    WHERE r.fetched = 1 AND r.tts_played = 1
                      AND r.created_at > UNIX_TIMESTAMP() - 300
                    ORDER BY r.id ASC
                    LIMIT 10
                """
                cur.execute(sql)
                rows = cur.fetchall()
                return [{"id": row[0]} for row in rows]
        except Exception as e:
            logger.error("Fetch (play) error: %s", e)
            return []
        finally:
            conn.close()

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
            logger.info(
                "Audio saved to %s (install sounddevice+soundfile for playback)",
                out_path,
            )

    def _mark_tts_delivered(self, response_id: int):
        """Поставить tts_played=2 (звук воспроизведён, цикл завершён)."""
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ai_responses SET tts_played = 2 WHERE id = %s",
                    (response_id,),
                )
        except Exception as e:
            logger.error("Failed to mark tts_delivered: %s", e)
        finally:
            conn.close()

    # ═══════════════════════════════════════════════════════════════════
    # ВСПОМОГАТЕЛЬНЫЕ
    # ═══════════════════════════════════════════════════════════════════
    def _get_bot_info(self, bot_guid: int) -> Optional[dict]:
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT race, gender FROM characters WHERE guid = %s",
                    (bot_guid,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                race_map = {
                    1: "Human",
                    2: "Orc",
                    3: "Dwarf",
                    4: "Night Elf",
                    5: "Undead",
                    6: "Tauren",
                    7: "Gnome",
                    8: "Troll",
                    10: "Blood Elf",
                    11: "Draenei",
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

    # ═══════════════════════════════════════════════════════════════════
    # ГЛАВНЫЙ ЦИКЛ
    # ═══════════════════════════════════════════════════════════════════
    def run(self):
        logger.info("TTS Worker v2.1 started. Mode: SYNC + fresh-first")
        while self._running:
            try:
                # === ЭТАП 1: Генерация голоса ===
                to_generate = self._fetch_to_generate()
                for resp in to_generate:
                    logger.info("[GEN] Starting id=%d (%d chars)", resp["id"], len(resp["text"]))
                    info = self._get_bot_info(resp["npc_guid"])
                    if info:
                        audio_data = self._generate_tts(
                            resp["text"], info["race"], info["gender"]
                        )
                    else:
                        audio_data = self._generate_tts(
                            resp["text"], "Human", "Male"
                        )

                    if audio_data:
                        self._audio_cache[resp["id"]] = audio_data
                        self._mark_tts_ready(resp["id"])
                        logger.info("[GEN] id=%d ready (cached)", resp["id"])
                    else:
                        # Генерация провалилась — пропускаем звук, даём Lua показать текст
                        logger.warning(
                            "[GEN] id=%d FAILED, marking ready without audio", resp["id"]
                        )
                        self._mark_tts_ready(resp["id"])

                # === ЭТАП 2: Воспроизведение звука ===
                to_play = self._fetch_to_play()
                for resp in to_play:
                    audio_data = self._audio_cache.pop(resp["id"], None)
                    if audio_data:
                        logger.info("[PLAY] Playing id=%d", resp["id"])
                        self._play_audio(audio_data)
                        self._mark_tts_delivered(resp["id"])
                        logger.info("[PLAY] id=%d delivered", resp["id"])
                    else:
                        # Аудио потерялось из кэша (перезапуск воркера?)
                        logger.warning(
                            "[PLAY] Cache miss for id=%d, skipping audio", resp["id"]
                        )
                        self._mark_tts_delivered(resp["id"])

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

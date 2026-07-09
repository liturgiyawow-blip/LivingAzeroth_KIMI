import json
import time
import threading
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

class WoWDBBridge:
    def __init__(self):
        self.request_file = Path("C:/Data/Programs/LivingAzeroth/bridge/ai_requests.jsonl")
        self.response_file = Path("C:/Data/Programs/LivingAzeroth/bridge/ai_responses.jsonl")
        self._running = True
        self._callbacks = []
        self._last_position = 0
    
    def start(self):
        threading.Thread(target=self._poll_loop, daemon=True, name="FilePoller").start()
        logger.info("File bridge started")
    
    def register_callback(self, callback):
        self._callbacks.append(callback)
    
    def _poll_loop(self):
        while self._running:
            try:
                if self.request_file.exists():
                    with open(self.request_file, "r", encoding="utf-8") as f:
                        f.seek(self._last_position)
                        for line in f:
                            line = line.strip()
                            if line:
                                try:
                                    req = json.loads(line)
                                    logger.info("Request: %s -> %s: '%s'", 
                                        req.get("player_name"), req.get("npc_name"), req.get("message"))
                                    for cb in self._callbacks:
                                        cb(req)
                                except json.JSONDecodeError:
                                    pass
                        self._last_position = f.tell()
            except Exception as e:
                logger.error("Poll error: %s", e)
            
            time.sleep(0.3)

    def write_response(self, npc_entry, npc_guid, response_text, emote_id=0, mood_change=None, set_flag=None):
        import os
        resp = {
            "npc_guid": npc_guid,
            "npc_entry": npc_entry,
            "response_text": response_text,
            "emote_id": emote_id,
        }
        try:
            # Открываем с buffering=1 (line-buffered) + fsync
            with open(self.response_file, "a", encoding="utf-8", buffering=1) as f:
                f.write(json.dumps(resp, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
            
            logger.info("Response written: npc_guid=%d", npc_guid)
        except Exception as e:
            logger.error("Failed to write response: %s", e)

    def shutdown(self):
        self._running = False
    
"""
WorldState — потокобезопасная RAM-first БД мира
Адаптировано под AzerothCore: creatures, players, world_events
"""

import json
import threading
import time
import atexit
import gc
import sys
from collections import deque
from pathlib import Path
from typing import Any, Optional

import logging

logger = logging.getLogger(__name__)


class WorldState:
    def __init__(self, filepath: Path = None):
        self._filepath = filepath or Path("data/live_world_state.json")
        self._filepath.parent.mkdir(parents=True, exist_ok=True)
        
        self._lock = threading.RLock()
        self._data = {}
        self._dirty = False
        self._timer = None
        
        # Загрузка или создание дефолта
        self._load_or_create()
        
        # Автосохранение
        self._start_auto_save()
        atexit.register(self.force_save)
        
        logger.info("WorldState initialized. Size: %.2f MB", self.get_size_mb())
    
    def _create_default_state(self) -> dict:
        return {
            "meta": {
                "version": "1.0-wow",
                "last_save": "",
                "world_day": 1,
                "world_hour": 14,
            },
            "chronology": {
                "global": deque(maxlen=15),
                "maxlen": 15,
            },
            "creatures": {},
            "players": {},
            "world_events": {
                "active_invasions": [],
                "weather": "sunny",
                "economy_index": 1.0,
            },
        }
    
    def _load_or_create(self):
        if self._filepath.exists():
            try:
                with open(self._filepath, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                # Восстановить deque
                raw["chronology"]["global"] = deque(
                    raw["chronology"].get("global", []),
                    maxlen=raw["chronology"].get("maxlen", 15)
                )
                self._data = raw
                logger.info("Loaded world state from %s", self._filepath)
            except Exception as e:
                logger.error("Failed to load state: %s. Creating default.", e)
                self._data = self._create_default_state()
                self._dirty = True
        else:
            self._data = self._create_default_state()
            self._dirty = True
    
    def _save_to_disk(self):
        with self._lock:
            if not self._dirty:
                return
            
            # Конвертировать deque в list для JSON
            data_copy = self._deques_to_lists(self._data)
            data_copy["meta"]["last_save"] = time.strftime("%Y-%m-%d %H:%M:%S")
            
            try:
                with open(self._filepath, "w", encoding="utf-8") as f:
                    json.dump(data_copy, f, ensure_ascii=False, indent=2)
                self._dirty = False
                logger.debug("Saved world state to disk")
            except Exception as e:
                logger.error("Failed to save state: %s", e)
    
    def _deques_to_lists(self, obj: Any) -> Any:
        if isinstance(obj, deque):
            return list(obj)
        elif isinstance(obj, dict):
            return {k: self._deques_to_lists(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._deques_to_lists(v) for v in obj]
        return obj
    
    def _start_auto_save(self):
        def auto_save():
            self._save_to_disk()
            self._timer = threading.Timer(60.0, auto_save)
            self._timer.daemon = True
            self._timer.start()
        
        self._timer = threading.Timer(60.0, auto_save)
        self._timer.daemon = True
        self._timer.start()
    
    def get(self, key: str, default=None):
        with self._lock:
            return self._data.get(key, default)
    
    def set(self, key: str, value: Any):
        with self._lock:
            self._data[key] = value
            self._dirty = True
    
    def get_nested(self, path: str, default=None):
        with self._lock:
            keys = path.split(".")
            current = self._data
            for k in keys:
                if isinstance(current, dict) and k in current:
                    current = current[k]
                else:
                    return default
            return current
    
    def set_nested(self, path: str, value: Any):
        with self._lock:
            keys = path.split(".")
            current = self._data
            for k in keys[:-1]:
                if k not in current:
                    current[k] = {}
                current = current[k]
            current[keys[-1]] = value
            self._dirty = True
    
    def append_chronology(self, entry: str):
        with self._lock:
            self._data["chronology"]["global"].append(entry)
            self._dirty = True
    
    def get_citizen(self, name: str) -> Optional[dict]:
        return self.get_nested(f"creatures.{name}")
    
    def update_citizen(self, name: str, updates: dict):
        with self._lock:
            if name not in self._data["creatures"]:
                self._data["creatures"][name] = {}
            self._data["creatures"][name].update(updates)
            self._dirty = True
    
    def get_full_context(self, npc_name: str = None) -> dict:
        with self._lock:
            ctx = {
                "meta": dict(self._data.get("meta", {})),
                "chronology": list(self._data.get("chronology", {}).get("global", [])),
                "world_events": dict(self._data.get("world_events", {})),
            }
            if npc_name:
                ctx["npc"] = dict(self._data.get("creatures", {}).get(npc_name, {}))
            return ctx
    
    def get_size_mb(self) -> float:
        with self._lock:
            size = sys.getsizeof(self._data)
            # Примерная оценка вложенных объектов
            if size > 10_485_760:  # 10 MB
                gc.collect()
            return size / (1024 * 1024)
    
    def force_save(self):
        logger.info("Force saving world state...")
        self._save_to_disk()
    
    def shutdown(self):
        if self._timer:
            self._timer.cancel()
        self.force_save()
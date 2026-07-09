"""
EventBus — внутренняя шина событий
Модули общаются только через неё, не знают друг о друга.
"""

import threading
import logging
from typing import Callable, Dict, List

logger = logging.getLogger(__name__)


class EventBus:
    def __init__(self):
        self._handlers: Dict[str, List[Callable]] = {}
        self._lock = threading.Lock()
    
    def subscribe(self, event_type: str, handler: Callable):
        with self._lock:
            if event_type not in self._handlers:
                self._handlers[event_type] = []
            self._handlers[event_type].append(handler)
            logger.debug("Handler subscribed to %s", event_type)
    
    def publish(self, event_type: str, payload: dict):
        with self._lock:
            handlers = self._handlers.get(event_type, []).copy()
        
        for handler in handlers:
            try:
                handler(payload)
            except Exception as e:
                logger.error("Error in handler for %s: %s", event_type, e)
                # Другие обработчики продолжают работать
    
    def get_subscribers(self, event_type: str) -> List[Callable]:
        with self._lock:
            return self._handlers.get(event_type, []).copy()
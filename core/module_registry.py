"""
ModuleRegistry — регистратор модулей
"""

import logging
from typing import Dict, Callable, Optional

logger = logging.getLogger(__name__)


class ModuleRegistry:
    def __init__(self, app, world_state, llm_queue, event_bus):
        self.app = app
        self.world = world_state
        self.llm = llm_queue
        self.bus = event_bus
        self._handlers: Dict[str, Callable] = {}
    
    def register_module(self, name: str, handler):
        self._handlers[name] = handler
        logger.info("Module registered: %s", name)
    
    def get_handler(self, name: str) -> Optional[Callable]:
        return self._handlers.get(name)
    
    def add_route(self, url: str, view_func, methods=None):
        methods = methods or ["POST"]
        self.app.route(url, methods=methods)(view_func)
        logger.debug("Route added: %s %s", methods, url)
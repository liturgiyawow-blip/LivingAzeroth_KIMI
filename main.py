"""
Living Azeroth — главный файл
Запуск: python main.py
"""

import logging
import sys
from pathlib import Path

# Добавить папку проекта в путь
sys.path.insert(0, str(Path(__file__).parent))

from flask import Flask, request, jsonify

import config
from core.world_state import WorldState
from core.llm_queue import PriorityLLMQueue
from core.event_bus import EventBus
from core.module_registry import ModuleRegistry
from wow_connector.db_bridge import WoWDBBridge
from modules.creature_ai.handlers import CreatureAIHandler

# ─── ЛОГИРОВАНИЕ ───
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger("main")

# ─── ИНИЦИАЛИЗАЦИЯ ───
logger.info("=" * 50)
logger.info("Living Azeroth starting...")
logger.info("=" * 50)

app = Flask(__name__)

# Ядро
world_state = WorldState(config.WORLD_STATE_FILE)
llm_queue = PriorityLLMQueue(config.LLM_BASE_URL)
event_bus = EventBus()

# Мост к WoW (MySQL)
db_bridge = WoWDBBridge()

# Реестр модулей
registry = ModuleRegistry(app, world_state, llm_queue, event_bus)

# Модуль Creature AI (NPC + боты)
creature_handler = CreatureAIHandler(world_state, llm_queue, event_bus, db_bridge)
registry.register_module("creature_ai", creature_handler)

# Запуск DB Bridge (начать polling MySQL)
db_bridge.start()

# ─── ЭНДПОИНТЫ ───

@app.route("/health")
def health():
    if request.remote_addr not in config.ALLOWED_HOSTS:
        return jsonify({"error": "Forbidden"}), 403
    
    current = llm_queue.get_current_task_info()
    return jsonify({
        "status": "ok",
        "queue_size": llm_queue.get_queue_size(),
        "current_task": current,
        "stats": llm_queue.get_stats(),
        "world_state_size_mb": round(world_state.get_size_mb(), 2),
        "modules_loaded": list(registry._handlers.keys()),
    })

@app.route("/world/state")
def get_world_state():
    if request.remote_addr not in config.ALLOWED_HOSTS:
        return jsonify({"error": "Forbidden"}), 403
    
    raw = world_state._deques_to_lists(world_state._data)
    return jsonify(raw)

@app.route("/admin/save_state", methods=["POST"])
def force_save():
    if request.remote_addr not in config.ALLOWED_HOSTS:
        return jsonify({"error": "Forbidden"}), 403
    
    world_state.force_save()
    return jsonify({
        "status": "ok",
        "file": str(config.WORLD_STATE_FILE),
        "size_kb": round(config.WORLD_STATE_FILE.stat().st_size / 1024, 2) if config.WORLD_STATE_FILE.exists() else 0,
    })

# ─── ЗАПУСК ───
if __name__ == "__main__":
    logger.info("Server ready. LM Studio: %s", config.LLM_BASE_URL)
    logger.info("MySQL: %s:%d/%s", config.MYSQL_HOST, config.MYSQL_PORT, config.MYSQL_DB_CHARACTERS)
    logger.info("Press Ctrl+C to stop")
    
    try:
        app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        db_bridge.shutdown()
        llm_queue.shutdown()
        world_state.shutdown()
        logger.info("Goodbye")
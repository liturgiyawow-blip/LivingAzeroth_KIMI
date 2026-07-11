"""
Living Azeroth — главный файл (Вариант А: Живые NPC)
Запуск: python main.py

Убрано: TacticalAI (боты, планы, команды)
Добавлено: NPC профили, квесты, репутация, память
"""

import json
import logging
import sys
from pathlib import Path

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
logger.info("Living Azeroth [NPC-ONLY] starting...")
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

# Модуль Creature AI (NPC только)
creature_handler = CreatureAIHandler(world_state, llm_queue, event_bus, db_bridge)
registry.register_module("creature_ai", creature_handler)
logger.info("CreatureAIHandler registered (NPC-only mode)")

# Запуск DB Bridge (начать polling MySQL)
db_bridge.start()

# ─── ЭНДПОИНТЫ ───

@app.route("/health")
def health():
    if request.remote_addr not in config.ALLOWED_HOSTS:
        return jsonify({"error": "Forbidden"}), 403

    current = llm_queue.get_current_task_info()
    stats = llm_queue.get_stats()

    return jsonify({
        "status": "ok",
        "mode": "npc-only",
        "queue_size": llm_queue.get_queue_size(),
        "current_task": current,
        "stats": stats,
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

# ═══════════════════════════════════════════════════════════════════
# НОВОЕ: Эндпоинты для NPC-режима
# ═══════════════════════════════════════════════════════════════════

@app.route("/npc/<int:npc_guid>/memory")
def get_npc_memory(npc_guid):
    """Получить память NPC (все диалоги или с конкретным игроком)."""
    if request.remote_addr not in config.ALLOWED_HOSTS:
        return jsonify({"error": "Forbidden"}), 403

    player_guid = request.args.get("player_guid", type=int)
    limit = request.args.get("limit", 20, type=int)

    memory = db_bridge.get_memory(npc_guid, player_guid, limit)
    return jsonify({
        "npc_guid": npc_guid,
        "player_guid": player_guid,
        "memory": memory,
        "count": len(memory),
    })

@app.route("/npc/<int:npc_guid>/reputation/<int:player_guid>")
def get_npc_reputation(npc_guid, player_guid):
    """Получить репутацию игрока у NPC."""
    if request.remote_addr not in config.ALLOWED_HOSTS:
        return jsonify({"error": "Forbidden"}), 403

    rep = db_bridge.get_reputation(npc_guid, player_guid)
    return jsonify({
        "npc_guid": npc_guid,
        "player_guid": player_guid,
        "reputation": rep,
    })

@app.route("/quests")
def list_quests():
    """Список доступных квестов."""
    if request.remote_addr not in config.ALLOWED_HOSTS:
        return jsonify({"error": "Forbidden"}), 403

    conn = None
    try:
        conn = db_bridge._get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT quest_id, quest_name, giver_npc_entry, giver_npc_name,
                       required_item_count, reward_gold, reward_reputation
                FROM npc_quests
                ORDER BY quest_id
            """)
            rows = cur.fetchall()
            quests = []
            for row in rows:
                quests.append({
                    "quest_id": row[0],
                    "quest_name": row[1],
                    "giver_npc_entry": row[2],
                    "giver_npc_name": row[3],
                    "required_item_count": row[4],
                    "reward_gold": row[5],
                    "reward_reputation": row[6],
                })
            return jsonify({"quests": quests, "count": len(quests)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn is not None:
            conn.close()

@app.route("/player/<int:player_guid>/quests")
def get_player_quests(player_guid):
    """Получить прогресс квестов игрока."""
    if request.remote_addr not in config.ALLOWED_HOSTS:
        return jsonify({"error": "Forbidden"}), 403

    conn = None
    try:
        conn = db_bridge._get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT pqp.quest_id, nq.quest_name, pqp.status,
                       pqp.item_count, pqp.npc_kills, pqp.given_at, pqp.completed_at
                FROM player_quest_progress pqp
                JOIN npc_quests nq ON pqp.quest_id = nq.quest_id
                WHERE pqp.player_guid = %s
                ORDER BY pqp.given_at DESC
            """, (player_guid,))
            rows = cur.fetchall()
            quests = []
            for row in rows:
                quests.append({
                    "quest_id": row[0],
                    "quest_name": row[1],
                    "status": row[2],
                    "item_count": row[3],
                    "npc_kills": row[4],
                    "given_at": row[5],
                    "completed_at": row[6],
                })
            return jsonify({"player_guid": player_guid, "quests": quests})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn is not None:
            conn.close()

@app.route("/admin/quest/<quest_id>/give", methods=["POST"])
def admin_give_quest(quest_id):
    """Выдать квест игроку (для тестирования)."""
    if request.remote_addr not in config.ALLOWED_HOSTS:
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json() or {}
    player_guid = data.get("player_guid")
    player_name = data.get("player_name", "Unknown")
    giver_npc_guid = data.get("giver_npc_guid", 0)

    if not player_guid:
        return jsonify({"error": "player_guid required"}), 400

    success = db_bridge.give_quest(player_guid, player_name, quest_id, giver_npc_guid)
    return jsonify({"success": success, "quest_id": quest_id, "player_guid": player_guid})


# ─── ЗАПУСК ───
if __name__ == "__main__":
    logger.info("Server ready. LM Studio: %s", config.LLM_BASE_URL)
    logger.info("MySQL: %s:%d/%s", config.MYSQL_HOST, config.MYSQL_PORT, config.MYSQL_DB_CHARACTERS)
    logger.info("Modules loaded: %s", list(registry._handlers.keys()))
    logger.info("Mode: NPC-ONLY (dialogs, memory, quests)")
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

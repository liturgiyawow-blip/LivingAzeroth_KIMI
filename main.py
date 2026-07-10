"""
Living Azeroth — главный файл
Запуск: python main.py

Этап 5: Интеграция TacticalAI (планировщик + исполнитель)
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

# ═══════════════════════════════════════════════════════════════════
# НОВОЕ (Этап 3): Импорт TacticalAI
# ═══════════════════════════════════════════════════════════════════
try:
    from modules.tactical_ai import create_tactical_planner, create_plan_executor
    TACTICAL_AI_AVAILABLE = True
except ImportError as e:
    TACTICAL_AI_AVAILABLE = False
    logging.warning("TacticalAI modules not available: %s", e)

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

# ═══════════════════════════════════════════════════════════════════
# НОВОЕ (Этап 3): Регистрация TacticalAI
# ═══════════════════════════════════════════════════════════════════
if TACTICAL_AI_AVAILABLE:
    logger.info("TacticalAI modules found, initializing...")

    # Планировщик — генерирует планы через LLM
    tactical_planner = create_tactical_planner(
        llm_queue, event_bus, db_bridge, world_state
    )
    registry.register_module("tactical_planner", tactical_planner)
    logger.info("TacticalPlanner registered")

    # Исполнитель — читает планы из БД и публикует события
    tactical_executor = create_plan_executor(
        db_bridge, event_bus, world_state
    )
    tactical_executor.start()
    registry.register_module("tactical_executor", tactical_executor)
    logger.info("PlanExecutor started and registered")

else:
    logger.warning("TacticalAI NOT available — tactic commands will be acknowledged but not executed")

# Запуск DB Bridge (начать polling MySQL)
db_bridge.start()

# ─── ЭНДПОИНТЫ ───

@app.route("/health")
def health():
    if request.remote_addr not in config.ALLOWED_HOSTS:
        return jsonify({"error": "Forbidden"}), 403

    current = llm_queue.get_current_task_info()
    stats = llm_queue.get_stats()

    # ═══════════════════════════════════════════════════════════════
    # НОВОЕ: Статистика TacticalAI
    # ═══════════════════════════════════════════════════════════════
    tactical_stats = {}
    if TACTICAL_AI_AVAILABLE:
        try:
            tactical_stats = {
                "executor_queue_size": tactical_executor.get_queue_size(),
                "executor_stats": tactical_executor.get_stats(),
            }
        except Exception as e:
            tactical_stats = {"error": str(e)}

    return jsonify({
        "status": "ok",
        "queue_size": llm_queue.get_queue_size(),
        "current_task": current,
        "stats": stats,
        "world_state_size_mb": round(world_state.get_size_mb(), 2),
        "modules_loaded": list(registry._handlers.keys()),
        "tactical_ai": tactical_stats,
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
# НОВОЕ (Этап 5): Эндпоинты для управления тактикой
# ═══════════════════════════════════════════════════════════════════

@app.route("/tactics/plans", methods=["GET"])
def list_tactic_plans():
    """Получить список активных тактических планов."""
    if request.remote_addr not in config.ALLOWED_HOSTS:
        return jsonify({"error": "Forbidden"}), 403

    try:
        conn = db_bridge._get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT plan_id, player_name, encounter_name, status, started_at
                FROM ai_tactic_plans
                WHERE status = 'active'
                ORDER BY started_at DESC
                LIMIT 20
            """)
            rows = cur.fetchall()
            plans = []
            for row in rows:
                plans.append({
                    "plan_id": row[0],
                    "player_name": row[1],
                    "encounter_name": row[2],
                    "status": row[3],
                    "started_at": row[4],
                })
            return jsonify({"plans": plans, "count": len(plans)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if 'conn' in locals():
            conn.close()


@app.route("/tactics/plan/<plan_id>", methods=["GET"])
def get_tactic_plan(plan_id):
    """Получить детали конкретного плана."""
    if request.remote_addr not in config.ALLOWED_HOSTS:
        return jsonify({"error": "Forbidden"}), 403

    try:
        conn = db_bridge._get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT plan_id, player_name, plan_json, status, started_at FROM ai_tactic_plans WHERE plan_id = %s",
                (plan_id,)
            )
            row = cur.fetchone()
            if not row:
                return jsonify({"error": "Plan not found"}), 404

            plan_data = json.loads(row[2]) if row[2] else {}

            # Получить шаги плана
            cur.execute("""
                SELECT bot_name, bot_role, phase_name, step_id, action, target, 
                       strategy_cmd, priority, executed, result_text
                FROM ai_tactics
                WHERE plan_id = %s
                ORDER BY phase_id, step_order
            """, (plan_id,))

            steps = []
            for s in cur.fetchall():
                steps.append({
                    "bot_name": s[0],
                    "bot_role": s[1],
                    "phase": s[2],
                    "step": s[3],
                    "action": s[4],
                    "target": s[5],
                    "strategy": s[6],
                    "priority": s[7],
                    "executed": s[8],
                    "result": s[9],
                })

            return jsonify({
                "plan_id": row[0],
                "player_name": row[1],
                "status": row[3],
                "started_at": row[4],
                "plan_data": plan_data,
                "steps": steps,
            })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if 'conn' in locals():
            conn.close()


@app.route("/tactics/cancel/<plan_id>", methods=["POST"])
def cancel_tactic_plan(plan_id):
    """Отменить активный план."""
    if request.remote_addr not in config.ALLOWED_HOSTS:
        return jsonify({"error": "Forbidden"}), 403

    if not TACTICAL_AI_AVAILABLE:
        return jsonify({"error": "TacticalAI not available"}), 503

    try:
        success = tactical_planner.cancel_plan(plan_id)
        if success:
            return jsonify({"status": "cancelled", "plan_id": plan_id})
        else:
            return jsonify({"error": "Failed to cancel plan"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── ЗАПУСК ───
if __name__ == "__main__":
    logger.info("Server ready. LM Studio: %s", config.LLM_BASE_URL)
    logger.info("MySQL: %s:%d/%s", config.MYSQL_HOST, config.MYSQL_PORT, config.MYSQL_DB_CHARACTERS)
    logger.info("Modules loaded: %s", list(registry._handlers.keys()))

    if TACTICAL_AI_AVAILABLE:
        logger.info("TacticalAI: ENABLED (planner + executor)")
    else:
        logger.info("TacticalAI: DISABLED")

    logger.info("Press Ctrl+C to stop")

    try:
        app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        # ═══════════════════════════════════════════════════════════
        # НОВОЕ: Корректное завершение TacticalAI
        # ═══════════════════════════════════════════════════════════
        if TACTICAL_AI_AVAILABLE:
            try:
                tactical_executor.shutdown()
                logger.info("PlanExecutor shutdown")
            except Exception as e:
                logger.error("Error shutting down executor: %s", e)

        db_bridge.shutdown()
        llm_queue.shutdown()
        world_state.shutdown()
        logger.info("Goodbye")
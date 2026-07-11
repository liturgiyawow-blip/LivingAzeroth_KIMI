"""
plan_executor.py — Исполнитель тактических планов (v1.1)

Этот модуль — "палочка дирижёра". Читает готовые шаги плана
из таблицы ai_tactics и превращает их в конкретные команды для ботов.

Архитектура:
  Вход:  таблица ai_tactics (Python пишет, executor читает)
  Выход: события через EventBus → Lua-контроллер исполняет

Работает в фоновом потоке: раз в 200мс проверяет новые шаги,
публикует команды через EventBus.
"""

from __future__ import annotations

import json
import time
import threading
import logging
from typing import Dict, List, Optional, Any
from collections import deque

# Импорты из проекта — используем относительные пути для надёжности
try:
    from core.llm_queue import PriorityLLMQueue
    from core.event_bus import EventBus
    from core.world_state import WorldState
    from wow_connector.db_bridge import WoWDBBridge
except ImportError:
    # Fallback: абсолютные импорты если запускаем не из корня
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from core.llm_queue import PriorityLLMQueue
    from core.event_bus import EventBus
    from core.world_state import WorldState
    from wow_connector.db_bridge import WoWDBBridge

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# КОНСТАНТЫ
# ═══════════════════════════════════════════════════════════════════

POLL_INTERVAL_MS = 200          # Как часто проверять БД (мс)
EMERGENCY_POLL_MS = 50          # Ускоренный polling при emergency
CLEANUP_INTERVAL_SEC = 300      # Как часто чистить старые планы (сек)
MAX_PENDING_STEPS = 100         # Анти-переполнение очереди


# ═══════════════════════════════════════════════════════════════════
# КЛАСС: PlanExecutor
# ═══════════════════════════════════════════════════════════════════

class PlanExecutor:
    """
    Исполнитель тактических планов.

    Как оператор диспетчерской: следит за доской заявок (ai_tactics),
    берёт новые заявки и передаёт их исполнителям (Lua-контроллер).
    """

    def __init__(
        self,
        db_bridge: WoWDBBridge,
        event_bus: EventBus,
        world_state: WorldState,
    ) -> None:
        self.db = db_bridge
        self.bus = event_bus
        self.world = world_state

        # Фоновый поток polling'а
        self._running = False
        self._worker: Optional[threading.Thread] = None

        # Очередь шагов для обработки (deque с лимитом — анти-утечка)
        self._pending_steps: deque = deque(maxlen=MAX_PENDING_STEPS)

        # Защита очереди
        self._queue_lock = threading.Lock()

        # Отслеживание выполненных шагов (чтобы не дублировать)
        self._executed_steps: Dict[tuple, float] = {}
        self._executed_lock = threading.Lock()

        # Счётчики статистики
        self._stats = {
            "steps_processed": 0,
            "steps_emergency": 0,
            "plans_completed": 0,
            "errors": 0,
        }
        self._stats_lock = threading.Lock()

        logger.info("PlanExecutor initialized")

    # ═══════════════════════════════════════════════════════════════
    # ЖИЗНЕННЫЙ ЦИКЛ
    # ═══════════════════════════════════════════════════════════════

    def start(self) -> None:
        """Запустить фоновый polling."""
        if self._running:
            logger.warning("Executor already running")
            return

        self._running = True
        self._worker = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="PlanExecutorPoller",
        )
        self._worker.start()
        logger.info("PlanExecutor polling started (%d ms)", POLL_INTERVAL_MS)

    def shutdown(self) -> None:
        """Остановить polling."""
        self._running = False
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=2.0)
        logger.info("PlanExecutor shutdown")

    # ═══════════════════════════════════════════════════════════════
    # ОСНОВНОЙ ЦИКЛ
    # ═══════════════════════════════════════════════════════════════

    def _poll_loop(self) -> None:
        """Бесконечный цикл проверки ai_tactics."""
        last_cleanup = time.time()

        while self._running:
            try:
                # 1. Прочитать новые шаги из БД
                new_steps = self._fetch_pending_steps()

                # 2. Добавить в очередь
                if new_steps:
                    with self._queue_lock:
                        for step in new_steps:
                            self._pending_steps.append(step)
                    logger.debug("Fetched %d new tactic steps", len(new_steps))

                # 3. Обработать очередь
                self._process_queue()

                # 4. Периодическая очистка
                if time.time() - last_cleanup > CLEANUP_INTERVAL_SEC:
                    self._cleanup_old_records()
                    last_cleanup = time.time()

            except Exception as e:
                logger.error("Poll loop error: %s", e)
                with self._stats_lock:
                    self._stats["errors"] += 1

            # Пауза между проверками
            interval = self._get_poll_interval()
            time.sleep(interval / 1000.0)

    def _get_poll_interval(self) -> int:
        """Определить интервал polling'а."""
        with self._queue_lock:
            for step in self._pending_steps:
                if step.get("priority") == "emergency":
                    return EMERGENCY_POLL_MS
        return POLL_INTERVAL_MS

    # ═══════════════════════════════════════════════════════════════
    # ЧТЕНИЕ ШАГОВ ИЗ БД
    # ═══════════════════════════════════════════════════════════════

    def _fetch_pending_steps(self) -> List[Dict[str, Any]]:
        """
        Прочитать невыполненные шаги из ai_tactics.

        Выбираем шаги со статусом executed=0 (ждут выполнения),
        сортируем по приоритету (emergency первыми) и времени создания.
        """
        steps: List[Dict[str, Any]] = []
        conn = None

        try:
            conn = self.db._get_conn()
            with conn.cursor() as cur:
                sql = """
                    SELECT 
                        id, plan_id, player_guid, player_name,
                        bot_guid, bot_name, bot_role,
                        phase_id, phase_name, step_id, step_order,
                        action, target, target_rti, strategy_cmd,
                        condition_json, priority, timeout_sec, created_at
                    FROM ai_tactics
                    WHERE executed = 0
                      AND (created_at + timeout_sec) > UNIX_TIMESTAMP()
                    ORDER BY 
                        FIELD(priority, 'emergency', 'manual', 'normal'),
                        created_at ASC
                    LIMIT 50
                """
                cur.execute(sql)
                rows = cur.fetchall()

                for row in rows:
                    (
                        id_, plan_id, p_guid, p_name, b_guid, b_name, b_role,
                        phase_id, phase_name, step_id, step_order,
                        action, target, target_rti, strategy_cmd,
                        condition_json, priority, timeout_sec, created_at,
                    ) = row

                    # Парсим condition_json обратно в dict
                    condition = None
                    if condition_json:
                        try:
                            condition = json.loads(condition_json)
                        except json.JSONDecodeError:
                            condition = None

                    steps.append({
                        "id": id_,
                        "plan_id": plan_id,
                        "player_guid": p_guid,
                        "player_name": p_name,
                        "bot_guid": b_guid,
                        "bot_name": b_name,
                        "bot_role": b_role,
                        "phase_id": phase_id,
                        "phase_name": phase_name,
                        "step_id": step_id,
                        "step_order": step_order,
                        "action": action,
                        "target": target,
                        "target_rti": target_rti,
                        "strategy_cmd": strategy_cmd,
                        "condition": condition,
                        "priority": priority,
                        "timeout_sec": timeout_sec,
                        "created_at": created_at,
                    })

                # Помечаем как "в обработке" (executed=1)
                if rows:
                    ids = [r[0] for r in rows]
                    placeholders = ",".join(["%s"] * len(ids))
                    cur.execute(
                        f"UPDATE ai_tactics SET executed = 1, executed_at = UNIX_TIMESTAMP() WHERE id IN ({placeholders})",
                        ids,
                    )

        except Exception as e:
            logger.error("Failed to fetch steps: %s", e)
        finally:
            if conn is not None:
                conn.close()

        return steps

    # ═══════════════════════════════════════════════════════════════
    # ОБРАБОТКА ОЧЕРЕДИ
    # ═══════════════════════════════════════════════════════════════

    def _process_queue(self) -> None:
        """
        Обработать накопленные шаги из очереди.

        Для каждого шага:
        1. Проверяем, не дубль ли
        2. Публикуем событие tactic_step_execute для Lua-контроллера
        3. Если шаг без условий — сразу помечаем выполненным
        """
        steps_to_process: List[Dict[str, Any]] = []

        with self._queue_lock:
            while self._pending_steps:
                steps_to_process.append(self._pending_steps.popleft())

        for step in steps_to_process:
            step_key = (step["plan_id"], step["step_id"], step["bot_guid"])

            # Проверяем дубли
            with self._executed_lock:
                if step_key in self._executed_steps:
                    logger.debug(
                        "Skip duplicate step %s for bot %s",
                        step["step_id"],
                        step["bot_name"],
                    )
                    continue
                self._executed_steps[step_key] = time.time()

            # Публикуем событие для Lua-контроллера
            self._publish_step(step)

            # Обновляем статистику
            with self._stats_lock:
                self._stats["steps_processed"] += 1
                if step.get("priority") == "emergency":
                    self._stats["steps_emergency"] += 1

    def _publish_step(self, step: Dict[str, Any]) -> None:
        """
        Опубликовать событие tactic_step_execute для Lua-контроллера.
        """
        command = self._build_bot_command(step)

        event_payload = {
            "event_type": "tactic_step_execute",
            "plan_id": step["plan_id"],
            "step_id": step["step_id"],
            "player_guid": step["player_guid"],
            "player_name": step["player_name"],
            "bot_guid": step["bot_guid"],
            "bot_name": step["bot_name"],
            "bot_role": step["bot_role"],
            "action": step["action"],
            "target": step["target"],
            "target_rti": step.get("target_rti"),
            "strategy_cmd": step.get("strategy_cmd"),
            "condition": step.get("condition"),
            "priority": step.get("priority"),
            "command_text": command,
            "timestamp": time.time(),
        }

        self.bus.publish("tactic_step_execute", event_payload)

        logger.info(
            "Published step %s for bot %s (action=%s, target=%s, priority=%s)",
            step["step_id"],
            step["bot_name"],
            step["action"],
            step["target"],
            step.get("priority", "normal"),
        )

        # Если шаг без условий — сразу помечаем выполненным в БД
        if not step.get("condition"):
            self._mark_step_completed(step["id"], "executed_immediately")

    # ═══════════════════════════════════════════════════════════════
    # ПОСТРОЕНИЕ КОМАНДЫ ДЛЯ БОТА
    # ═══════════════════════════════════════════════════════════════

    def _build_bot_command(self, step: Dict[str, Any]) -> str:
        """
        Превратить шаг плана в текстовую команду для playerbots.
        """
        action = step.get("action", "follow")
        target = step.get("target", "leader")
        strategy = step.get("strategy_cmd")
        target_rti = step.get("target_rti")

        # Если есть стратегия — отправляем её первой
        if strategy:
            return strategy

        # Преобразуем action в команду playerbots
        action_map = {
            "attack": "attack",
            "heal": f"cast Heal on {target}" if target != "leader" else "heal",
            "stay": "stay",
            "follow": "follow",
            "flee": "flee",
            "pull": "pull my target",
            "wait": "stay",
            "cast": f"cast {target}",
        }

        cmd = action_map.get(action, "follow")

        # Если указана RTI-метка
        if target_rti and action == "attack":
            cmd = "attack rti target"

        return cmd

    # ═══════════════════════════════════════════════════════════════
    # ОТМЕТКА ВЫПОЛНЕНИЯ В БД
    # ═══════════════════════════════════════════════════════════════

    def _mark_step_completed(self, step_id: int, result: str) -> None:
        """
        Пометить шаг как выполненный в ai_tactics.
        """
        conn = None
        try:
            conn = self.db._get_conn()
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE ai_tactics 
                       SET executed = 2, completed_at = UNIX_TIMESTAMP(), result_text = %s
                       WHERE id = %s""",
                    (result, step_id),
                )
        except Exception as e:
            logger.error("Failed to mark step %d completed: %s", step_id, e)
        finally:
            if conn is not None:
                conn.close()

    def mark_step_completed_by_lua(self, step_db_id: int, result: str = "completed") -> None:
        """
        Публичный метод для Lua-контроллера.
        """
        self._mark_step_completed(step_db_id, result)

    # ═══════════════════════════════════════════════════════════════
    # ОЧИСТКА СТАРЫХ ЗАПИСЕЙ
    # ═══════════════════════════════════════════════════════════════

    def _cleanup_old_records(self) -> None:
        """Удалить старые выполненные записи."""
        conn = None
        try:
            conn = self.db._get_conn()
            with conn.cursor() as cur:
                cur.execute(
                    """DELETE FROM ai_tactics 
                       WHERE executed IN (2, 3, 4) 
                       AND completed_at < UNIX_TIMESTAMP() - 3600
                       LIMIT 1000""",
                )
                deleted = cur.rowcount
                if deleted > 0:
                    logger.info("Cleaned up %d old tactic records", deleted)
        except Exception as e:
            logger.error("Cleanup error: %s", e)
        finally:
            if conn is not None:
                conn.close()

        # Чистим кэш выполненных шагов
        with self._executed_lock:
            cutoff = time.time() - 3600
            old_keys = [k for k, v in self._executed_steps.items() if v < cutoff]
            for k in old_keys:
                del self._executed_steps[k]

    # ═══════════════════════════════════════════════════════════════
    # СТАТИСТИКА
    # ═══════════════════════════════════════════════════════════════

    def get_stats(self) -> Dict[str, Any]:
        """Получить статистику работы executor."""
        with self._stats_lock:
            return dict(self._stats)

    def get_queue_size(self) -> int:
        """Сколько шагов ждёт обработки."""
        with self._queue_lock:
            return len(self._pending_steps)


# ═══════════════════════════════════════════════════════════════════
# ФАБРИКА
# ═══════════════════════════════════════════════════════════════════

def create_plan_executor(
    db_bridge: WoWDBBridge,
    event_bus: EventBus,
    world_state: WorldState,
) -> PlanExecutor:
    """
    Фабричная функция для создания PlanExecutor.
    """
    return PlanExecutor(db_bridge, event_bus, world_state)
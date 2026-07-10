"""
TacticalAI — модуль тактического управления ботами

Этот модуль отвечает за:
1. Классификацию сообщений (болтовня vs тактика) — classifier.py
2. Генерацию тактических планов через LLM — tactical_planner.py
3. Исполнение планов через команды playerbots — plan_executor.py

Архитектура: три слоя, как у дирижёра оркестра:
- Классификатор = слух дирижёра (что сказали)
- Планировщик = мозг дирижёра (как распределить)
- Исполнитель = палочка дирижёра (дать сигнал музыкантам)
"""

from .classifier import MessageClassifier
from .tactical_planner import TacticalPlanner, create_tactical_planner
from .plan_executor import PlanExecutor, create_plan_executor

__all__ = [
    "MessageClassifier",
    "TacticalPlanner",
    "PlanExecutor",
    "create_tactical_planner",
    "create_plan_executor",
]
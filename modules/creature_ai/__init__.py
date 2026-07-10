"""
CreatureAI — модуль диалогов с NPC и ботами

Теперь интегрирован с TacticalAI для обработки тактических команд.
"""

# Импорт классификатора (Этап 2)
try:
    from modules.tactical_ai.classifier import MessageClassifier
    TACTICAL_AI_AVAILABLE = True
except ImportError:
    TACTICAL_AI_AVAILABLE = False

# Импорт планировщика (Этап 3)
try:
    from modules.tactical_ai.tactical_planner import TacticalPlanner, create_tactical_planner
    PLANNER_AVAILABLE = True
except ImportError:
    PLANNER_AVAILABLE = False

# Импорт исполнителя (Этап 3)
try:
    from modules.tactical_ai.plan_executor import PlanExecutor, create_plan_executor
    EXECUTOR_AVAILABLE = True
except ImportError:
    EXECUTOR_AVAILABLE = False


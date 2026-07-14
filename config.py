"""
Living Azeroth — конфигурация
Боевая версия, без моков. Реальный LM Studio + Qwen 2.5 14B Q6_0
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ─── ПУТИ ───
BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"

DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

# ─── LLM (LM Studio) ───
LLM_BASE_URL = "http://localhost:1234/v1"
LLM_TIMEOUT = 30.0
LLM_MOCK_MODE = False
LLM_MODEL_NAME = "eva-abliterated-ties-qwen2.5-14b-i1@q6_k"

# ─── MySQL: Игровая база (AzerothCore) ───
MYSQL_HOST = "127.0.0.1"
MYSQL_PORT = 3306
MYSQL_USER = os.getenv("MYSQL_USER", "acore")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "acore")  # дефолт, если .env не найден
MYSQL_DB_WORLD = "acore_world"
MYSQL_DB_CHARACTERS = "acore_characters"  # сюда пишем наши таблицы

# ─── TTS — ВЫРЕЗАНО ───
TTS_ENABLED = False

# ─── WORLD STATE ───
WORLD_STATE_FILE = DATA_DIR / "live_world_state.json"
AUTO_SAVE_INTERVAL = 60.0

# ─── LOGGING ───
LOG_LEVEL = "INFO"
LOG_FILE = LOGS_DIR / "living_azeroth.log"

# ─── SECURITY ───
ALLOWED_HOSTS = {"127.0.0.1", "::1"}

# ─── PRIORITY LLM QUEUE ───
# 1 = микро (быстрые ответы), 2 = мезо (диалоги), 3 = макро (фон)
PRIORITY_TOKENS = {
    1: 120,   # Микро: диалоги
    2: 80,    # Мезо: события
    3: 200,   # Макро: фон
}

# ═══════════════════════════════════════════════════════════════════
# НОВОЕ: Настройка модели LM Studio
# ═══════════════════════════════════════════════════════════════════
LLM_MODEL_NAME = "eva-abliterated-ties-qwen2.5-14b-i1@q6_k"  # Имя модели в LM Studio
"""
Living Azeroth — конфигурация
Боевая версия, без моков. Реальный LM Studio + Qwen 2.5 14B Q6_0
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()  # Загружает переменные из .env файла

# ─── ПУТИ ───
BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"

DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

# ─── LLM (LM Studio) ───
LLM_BASE_URL = "http://localhost:1234/v1"
LLM_TIMEOUT = 30.0  # секунд
LLM_MOCK_MODE = False  # ← БОЕВОЙ РЕЖИМ, БЕЗ ЗАГЛУШЕК

# ─── MySQL (AzerothCore) ───
MYSQL_HOST = "127.0.0.1"
MYSQL_PORT = 3306
MYSQL_USER = os.getenv("MYSQL_USER", "acore")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "acore")  # дефолт, если .env не найден
MYSQL_DB_WORLD = "acore_world"
MYSQL_DB_CHARACTERS = "acore_characters"  # сюда пишем наши таблицы

# ─── TTS — ВЫРЕЗАНО ПОЛНОСТЬЮ ───
TTS_ENABLED = False

# ─── WORLD STATE ───
WORLD_STATE_FILE = DATA_DIR / "live_world_state.json"
AUTO_SAVE_INTERVAL = 60.0  # секунд

# ─── LOGGING ───
LOG_LEVEL = "INFO"
LOG_FILE = LOGS_DIR / "living_azeroth.log"

# ─── SECURITY ───
ALLOWED_HOSTS = {"127.0.0.1", "::1"}

# ─── PRIORITY LLM QUEUE ───
PRIORITY_TOKENS = {
    1: 120,   # Микро: диалоги
    2: 80,    # Мезо: события
    3: 200,   # Макро: фон
}
#!/usr/bin/env python3
"""
Полная диагностика системы LivingAzeroth
Выгружает ВСЕ: порты, пути, методы, конфиги
"""

import json
import os
import socket
import sys
from pathlib import Path

print("=" * 70)
print("LIVING AZEROTH — ПОЛНАЯ ДИАГНОСТИКА")
print("=" * 70)
print(f"Python: {sys.version}")
print(f"Platform: {sys.platform}")
print()

# ─── 1. ПУТИ И ФАЙЛЫ ───
print("=" * 70)
print("1. ПУТИ И ФАЙЛЫ")
print("=" * 70)

BASE_DIR = Path("C:/Data/Programs/LivingAzeroth")
BRIDGE_DIR = BASE_DIR / "bridge"
LUA_DIR = Path("E:/AzerothCore-x64-WotLK-25.04.26/lua_scripts")
CONFIG_DIR = Path("E:/AzerothCore-x64-WotLK-25.04.26/configs/modules")

paths = {
    "Python base": BASE_DIR,
    "Bridge dir": BRIDGE_DIR,
    "Lua scripts": LUA_DIR,
    "Configs": CONFIG_DIR,
    "Request file": BRIDGE_DIR / "ai_requests.jsonl",
    "Response file": BRIDGE_DIR / "ai_responses.jsonl",
    "World state": BASE_DIR / "data" / "live_world_state.json",
}

for name, path in paths.items():
    exists = path.exists() if path else False
    print(f"  {name:20s}: {path} {'[OK]' if exists else '[MISSING]'}")

print()

# ─── 2. ПОРТЫ ───
print("=" * 70)
print("2. ПРОВЕРКА ПОРТОВ")
print("=" * 70)

def check_port(host, port, name):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex((host, port))
        sock.close()
        status = "OPEN" if result == 0 else "CLOSED"
        print(f"  {name:20s}: {host}:{port} [{status}]")
        return result == 0
    except Exception as e:
        print(f"  {name:20s}: {host}:{port} [ERROR: {e}]")
        return False

lm_studio = check_port("127.0.0.1", 1234, "LM Studio")
flask = check_port("127.0.0.1", 5000, "Python Flask")
mysql = check_port("127.0.0.1", 3306, "MySQL")

print()

# ─── 3. КОНФИГИ AZEROTHCORE ───
print("=" * 70)
print("3. КОНФИГИ AZEROTHCORE")
print("=" * 70)

config_files = {
    "mod_ale": CONFIG_DIR / "mod_ale.conf",
    "worldserver": Path("E:/AzerothCore-x64-WotLK-25.04.26/configs/worldserver.conf"),
}

for name, path in config_files.items():
    if path.exists():
        print(f"\n  --- {name} ({path}) ---")
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and ("ALE" in line or "Eluna" in line or "ScriptPath" in line):
                    print(f"    {line}")
    else:
        print(f"  {name}: [NOT FOUND]")

print()

# ─── 4. LUA СКРИПТЫ ───
print("=" * 70)
print("4. LUA СКРИПТЫ")
print("=" * 70)

if LUA_DIR.exists():
    for f in sorted(LUA_DIR.glob("*.lua")):
        size = f.stat().st_size
        print(f"  {f.name:30s} ({size} bytes)")
else:
    print("  [DIRECTORY NOT FOUND]")

print()

# ─── 5. МОСТОВЫЕ ФАЙЛЫ ───
print("=" * 70)
print("5. СОДЕРЖИМОЕ МОСТОВЫХ ФАЙЛОВ")
print("=" * 70)

for name, path in [("Requests", paths["Request file"]), ("Responses", paths["Response file"])]:
    print(f"\n--- {name} ---")
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        print(f"Lines: {len(lines)}")
        for i, line in enumerate(lines[-3:], 1):
            print(f"  {i}: {line.strip()[:100]}")
    else:
        print("  [EMPTY]")

print()

# ─── 6. PYTHON ЗАВИСИМОСТИ ───
print("=" * 70)
print("6. PYTHON ЗАВИСИМОСТИ")
print("=" * 70)

try:
    import flask
    print(f"  Flask: {flask.__version__}")
except:
    print("  Flask: [NOT INSTALLED]")

try:
    import requests
    print(f"  requests: {requests.__version__}")
except:
    print("  requests: [NOT INSTALLED]")

try:
    import pymysql
    print(f"  PyMySQL: {pymysql.__version__}")
except:
    print("  PyMySQL: [NOT INSTALLED]")

print()

# ─── 7. РЕКОМЕНДАЦИИ ───
print("=" * 70)
print("7. РЕКОМЕНДАЦИИ")
print("=" * 70)

issues = []
if not lm_studio:
    issues.append("LM Studio не отвечает на порту 1234")
if not flask:
    issues.append("Flask сервер не запущен на порту 5000")
if not mysql:
    issues.append("MySQL не отвечает на порту 3306")
if not paths["Request file"].exists():
    issues.append("Файл запросов не создан")
if not LUA_DIR.exists():
    issues.append("Папка lua_scripts не найдена")

if issues:
    for issue in issues:
        print(f"  ! {issue}")
else:
    print("  Все системы в норме")

print()
print("=" * 70)
print("ДИАГНОСТИКА ЗАВЕРШЕНА")
print("=" * 70)
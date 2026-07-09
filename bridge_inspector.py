import json
import time
from pathlib import Path

BRIDGE_DIR = Path("C:/Data/Programs/LivingAzeroth/bridge")
REQ_FILE = BRIDGE_DIR / "ai_requests.jsonl"
RESP_FILE = BRIDGE_DIR / "ai_responses.jsonl"

print("=" * 60)
print("BRIDGE INSPECTOR")
print("=" * 60)
print(f"Bridge dir: {BRIDGE_DIR}")
print(f"Requests:   {REQ_FILE} (exists: {REQ_FILE.exists()})")
print(f"Responses:  {RESP_FILE} (exists: {RESP_FILE.exists()})")
print()

# Читаем requests
if REQ_FILE.exists():
    print("--- REQUESTS (from Lua) ---")
    with open(REQ_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
    print(f"Total lines: {len(lines)}")
    for i, line in enumerate(lines[-3:], 1):  # последние 3
        line = line.strip()
        if line:
            try:
                data = json.loads(line)
                print(f"\nLine {i}: {json.dumps(data, ensure_ascii=False, indent=2)}")
            except:
                print(f"\nLine {i} (RAW): {line[:100]}")
else:
    print("--- NO REQUESTS FILE ---")

print()
print("=" * 60)

# Читаем responses
if RESP_FILE.exists():
    print("--- RESPONSES (from Python) ---")
    with open(RESP_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
    print(f"Total lines: {len(lines)}")
    for i, line in enumerate(lines[-3:], 1):  # последние 3
        line = line.strip()
        if line:
            try:
                data = json.loads(line)
                print(f"\nLine {i}: {json.dumps(data, ensure_ascii=False, indent=2)}")
            except:
                print(f"\nLine {i} (RAW): {line[:100]}")
else:
    print("--- NO RESPONSES FILE ---")

print()
print("=" * 60)
print("DIAGNOSTICS:")
print("=" * 60)

# Проверяем совпадение GUID
if REQ_FILE.exists() and RESP_FILE.exists():
    with open(REQ_FILE, "r") as f:
        req_lines = [json.loads(l) for l in f if l.strip()]
    with open(RESP_FILE, "r") as f:
        resp_lines = [json.loads(l) for l in f if l.strip()]
    
    req_guids = {r.get("npc_guid", r.get("ng")) for r in req_lines}
    resp_guids = {r.get("npc_guid", r.get("ng")) for r in resp_lines}
    
    print(f"Request NPC GUIDs:  {req_guids}")
    print(f"Response NPC GUIDs: {resp_guids}")
    print(f"Match: {req_guids & resp_guids}")
    print(f"Missing in responses: {req_guids - resp_guids}")
    
    # Проверяем ключи
    if req_lines:
        print(f"\nRequest keys: {list(req_lines[-1].keys())}")
    if resp_lines:
        print(f"Response keys: {list(resp_lines[-1].keys())}")
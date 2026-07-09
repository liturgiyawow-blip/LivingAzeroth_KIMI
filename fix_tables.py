#!/usr/bin/env python3
import pymysql

HOST = "127.0.0.1"
PORT = 3306
USER = "acore"
PASSWORD = "acore"
DB = "acore_characters"

print("Подключаюсь к MySQL...")
conn = pymysql.connect(host=HOST, port=PORT, user=USER, password=PASSWORD, database=DB)
cursor = conn.cursor()

# Список команд: (SQL, описание)
commands = [
    # ai_requests
    ("ALTER TABLE ai_requests CHANGE COLUMN channel channel_type VARCHAR(20) DEFAULT 'SAY'", "rename channel→channel_type"),
    ("ALTER TABLE ai_requests ADD COLUMN target_is_player TINYINT(1) DEFAULT 0", "add target_is_player"),
    
    # ai_responses
    ("ALTER TABLE ai_responses ADD COLUMN player_guid INT UNSIGNED NOT NULL DEFAULT 0", "add player_guid"),
    ("ALTER TABLE ai_responses ADD COLUMN fetched TINYINT(1) DEFAULT 0", "add fetched"),
    ("ALTER TABLE ai_responses ADD COLUMN delivered_at INT UNSIGNED DEFAULT 0", "add delivered_at"),
    ("ALTER TABLE ai_responses ADD COLUMN action_command VARCHAR(100) DEFAULT NULL", "add action_command"),
    
    # Индексы
    ("CREATE INDEX idx_fetched ON ai_responses(fetched)", "create idx_fetched"),
    ("CREATE INDEX idx_npc_guid ON ai_responses(npc_guid)", "create idx_npc_guid"),
]

for sql, desc in commands:
    try:
        cursor.execute(sql)
        print(f"✅ OK: {desc}")
    except pymysql.err.OperationalError as e:
        err_code, err_msg = e.args
        if err_code == 1060:  # Duplicate column
            print(f"⚠️  Уже есть: {desc}")
        elif err_code == 1061:  # Duplicate key (index)
            print(f"⚠️  Индекс уже есть: {desc}")
        elif err_code == 1054:  # Unknown column (для CHANGE COLUMN если уже переименовано)
            print(f"⚠️  Колонка уже переименована или не найдена: {desc}")
        else:
            print(f"❌ Ошибка ({err_code}) в '{desc}': {err_msg}")
    except Exception as e:
        print(f"❌ Ошибка в '{desc}': {e}")

conn.commit()
cursor.close()
conn.close()
print("\n✅ Готово! Запусти: python check_tables.py")
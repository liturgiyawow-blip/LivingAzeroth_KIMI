#!/usr/bin/env python3
"""
Living Azeroth — проверка таблиц MySQL
Запуск: python check_tables.py
"""

import pymysql
import sys

# Настройки подключения (как в твоём config.py)
HOST = "127.0.0.1"
PORT = 3306
USER = "acore"
PASSWORD = "acore"
DB = "acore_characters"

print("=" * 60)
print("LIVING AZEROTH — ПРОВЕРКА ТАБЛИЦ MySQL")
print("=" * 60)
print(f"Подключаюсь к: {HOST}:{PORT}/{DB}")
print(f"Пользователь: {USER}")
print()

try:
    conn = pymysql.connect(
        host=HOST,
        port=PORT,
        user=USER,
        password=PASSWORD,
        database=DB,
        charset='utf8mb4'
    )
    cursor = conn.cursor()
    print("✅ Подключение к MySQL УСПЕШНО")
    print()
except Exception as e:
    print(f"❌ ОШИБКА ПОДКЛЮЧЕНИЯ: {e}")
    print()
    print("Что проверить:")
    print("  1. Запущен ли MySQL? (диспетчер задач → mysqld.exe)")
    print("  2. Правильные ли логин/пароль? (по умолчанию: acore / acore)")
    print("  3. Правильный ли порт? (обычно 3306)")
    sys.exit(1)

# ============================================
# Проверяем ai_requests
# ============================================
print("-" * 60)
print("ТАБЛИЦА: ai_requests")
print("-" * 60)

cursor.execute("""
    SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES 
    WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'ai_requests'
""", (DB,))

if cursor.fetchone():
    print("✅ Таблица ai_requests НАЙДЕНА")
    
    cursor.execute("DESCRIBE ai_requests")
    columns = cursor.fetchall()
    
    print(f"\nСтолбцы ({len(columns)} штук):")
    print(f"{'Имя':<25} {'Тип':<25} {'NULL':<8} {'Ключ':<10} {'Дефолт':<15}")
    print("-" * 85)
    
    for col in columns:
        name, type_, null, key, default, extra = col
        print(f"{name:<25} {type_:<25} {null:<8} {key:<10} {str(default):<15}")
    
    # Проверяем нужные поля
    col_names = [c[0] for c in columns]
    needed = {
        'channel_type': 'VARCHAR(20)',
        'target_is_player': 'TINYINT(1)',
        'created_at': 'INT'
    }
    
    print("\nПроверка нужных полей:")
    for field, expected_type in needed.items():
        if field in col_names:
            print(f"  ✅ {field} — ЕСТЬ")
        else:
            print(f"  ❌ {field} — НЕТ (нужно добавить!)")
            
else:
    print("❌ Таблица ai_requests НЕ НАЙДЕНА")
    print("   Нужно создать! (SQL ниже)")

print()

# ============================================
# Проверяем ai_responses
# ============================================
print("-" * 60)
print("ТАБЛИЦА: ai_responses")
print("-" * 60)

cursor.execute("""
    SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES 
    WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'ai_responses'
""", (DB,))

if cursor.fetchone():
    print("✅ Таблица ai_responses НАЙДЕНА")
    
    cursor.execute("DESCRIBE ai_responses")
    columns = cursor.fetchall()
    
    print(f"\nСтолбцы ({len(columns)} штук):")
    print(f"{'Имя':<25} {'Тип':<25} {'NULL':<8} {'Ключ':<10} {'Дефолт':<15}")
    print("-" * 85)
    
    for col in columns:
        name, type_, null, key, default, extra = col
        print(f"{name:<25} {type_:<25} {null:<8} {key:<10} {str(default):<15}")
    
    col_names = [c[0] for c in columns]
    needed = {
        'created_at': 'INT',
        'delivered_at': 'INT',
        'fetched': 'TINYINT(1)'
    }
    
    print("\nПроверка нужных полей:")
    for field, expected_type in needed.items():
        if field in col_names:
            print(f"  ✅ {field} — ЕСТЬ")
        else:
            print(f"  ❌ {field} — НЕТ (нужно добавить!)")
            
else:
    print("❌ Таблица ai_responses НЕ НАЙДЕНА")
    print("   Нужно создать! (SQL ниже)")

print()

# ============================================
# Если таблиц нет — выдаём SQL для создания
# ============================================
print("=" * 60)
print("SQL ДЛЯ СОЗДАНИЯ/ИСПРАВЛЕНИЯ ТАБЛИЦ")
print("=" * 60)
print("""
-- Запусти это в MySQL (через HeidiSQL, phpMyAdmin или консоль):

USE acore_characters;

-- Таблица запросов (от Lua к Python)
CREATE TABLE IF NOT EXISTS ai_requests (
    id INT AUTO_INCREMENT PRIMARY KEY,
    player_guid INT UNSIGNED NOT NULL,
    player_name VARCHAR(50) NOT NULL,
    npc_guid INT UNSIGNED NOT NULL,
    npc_entry INT UNSIGNED DEFAULT 0,
    npc_name VARCHAR(50) NOT NULL,
    message TEXT NOT NULL,
    channel_type VARCHAR(20) DEFAULT 'SAY',
    target_is_player TINYINT(1) DEFAULT 0,
    created_at INT UNSIGNED DEFAULT 0,
    processed TINYINT(1) DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Таблица ответов (от Python к Lua)
CREATE TABLE IF NOT EXISTS ai_responses (
    id INT AUTO_INCREMENT PRIMARY KEY,
    player_guid INT UNSIGNED NOT NULL,
    npc_guid INT UNSIGNED NOT NULL,
    response_text TEXT NOT NULL,
    emote_id INT UNSIGNED DEFAULT 0,
    action_command VARCHAR(100) DEFAULT NULL,
    fetched TINYINT(1) DEFAULT 0,
    created_at INT UNSIGNED DEFAULT 0,
    delivered_at INT UNSIGNED DEFAULT 0,
    INDEX idx_fetched (fetched),
    INDEX idx_npc_guid (npc_guid)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
""")

cursor.close()
conn.close()

print("\n" + "=" * 60)
print("ПРОВЕРКА ЗАВЕРШЕНА")
print("=" * 60)
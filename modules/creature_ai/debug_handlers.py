"""
debug_handlers.py — Проверка внутренней логики handlers.py

Этот скрипт имитирует то, что делает handlers.py при диалоге,
но выводит ПОШАГОВО, где может быть ошибка.
"""

import pymysql
import sys

DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "acore",
    "password": "acore",
    "database": "acore_characters",
    "charset": "utf8mb4",
}

def get_conn():
    return pymysql.connect(**DB_CONFIG)

def test_memory_write():
    """Проверить, можем ли мы вручную записать в npc_memory."""
    print("═" * 60)
    print("ТЕСТ 1: Ручная запись в npc_memory")
    print("═" * 60)
    
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # Пробуем вставить тестовую запись
            cur.execute("""
                INSERT INTO npc_memory 
                (npc_guid, npc_entry, player_guid, player_name, memory_type,
                 content, player_message, npc_response, mood_after, reputation_after,
                 created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, UNIX_TIMESTAMP())
            """, (
                9999,  # тестовый npc_guid
                9999,  # тестовый entry
                710,   # GUID Гвадлина
                "Гвадлин",
                "dialogue",
                "Тестовая запись",
                "Привет",
                "И тебе привет",
                "нейтральный",
                0
            ))
            
            print("✅ Запись вставлена успешно!")
            
            # Проверим
            cur.execute("SELECT * FROM npc_memory WHERE npc_guid = 9999")
            row = cur.fetchone()
            if row:
                print(f"✅ Запись найдена: {row}")
            else:
                print("❌ Запись НЕ найдена после вставки!")
                
    except Exception as e:
        print(f"❌ ОШИБКА при записи: {e}")
        print("   Возможно, таблица npc_memory не существует или нет прав!")
    finally:
        conn.close()

def test_reputation_write():
    """Проверить, можем ли мы вручную записать в npc_reputation."""
    print("\n" + "═" * 60)
    print("ТЕСТ 2: Ручная запись в npc_reputation")
    print("═" * 60)
    
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # Пробуем вставить или обновить
            cur.execute("""
                INSERT INTO npc_reputation 
                (npc_guid, npc_entry, player_guid, player_name, reputation,
                 reputation_rank, total_dialogues, last_interaction_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, UNIX_TIMESTAMP())
                ON DUPLICATE KEY UPDATE 
                    total_dialogues = total_dialogues + 1,
                    last_interaction_at = UNIX_TIMESTAMP()
            """, (
                2723,  # Stormwind Guard
                68,    # entry
                710,   # Гвадлин
                "Гвадлин",
                0,
                "neutral",
                1
            ))
            
            print("✅ Репутация записана/обновлена!")
            
            # Проверим
            cur.execute("""
                SELECT * FROM npc_reputation 
                WHERE npc_guid = 2723 AND player_guid = 710
            """)
            row = cur.fetchone()
            if row:
                print(f"✅ Запись найдена: player={row[4]}, dialogs={row[7]}")
            else:
                print("❌ Запись НЕ найдена!")
                
    except Exception as e:
        print(f"❌ ОШИБКА: {e}")
    finally:
        conn.close()

def test_quest_give():
    """Проверить выдачу квеста."""
    print("\n" + "═" * 60)
    print("ТЕСТ 3: Выдача квеста Гвадлину")
    print("═" * 60)
    
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # Проверим, есть ли квест
            cur.execute("SELECT * FROM npc_quests WHERE quest_id = 'wolves_goldshire'")
            quest = cur.fetchone()
            if not quest:
                print("❌ Квест 'wolves_goldshire' не найден в npc_quests!")
                return
            
            print(f"✅ Квест найден: '{quest[2]}' (giver entry={quest[4]})")
            
            # Пробуем выдать
            cur.execute("""
                INSERT INTO player_quest_progress 
                (player_guid, player_name, quest_id, status, given_by_npc_guid, given_at)
                VALUES (%s, %s, %s, 'active', %s, UNIX_TIMESTAMP())
                ON DUPLICATE KEY UPDATE 
                    status = 'active', 
                    given_at = UNIX_TIMESTAMP()
            """, (710, "Гвадлин", "wolves_goldshire", 2723))
            
            print("✅ Квест выдан!")
            
            # Проверим
            cur.execute("""
                SELECT * FROM player_quest_progress 
                WHERE player_guid = 710 AND quest_id = 'wolves_goldshire'
            """)
            row = cur.fetchone()
            if row:
                print(f"✅ Прогресс найден: status={row[4]}, given_at={row[7]}")
            else:
                print("❌ Прогресс НЕ найден!")
                
    except Exception as e:
        print(f"❌ ОШИБКА: {e}")
    finally:
        conn.close()

def cleanup_test_data():
    """Удалить тестовые данные."""
    print("\n" + "═" * 60)
    print("ОЧИСТКА тестовых данных")
    print("═" * 60)
    
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM npc_memory WHERE npc_guid = 9999")
            cur.execute("DELETE FROM npc_reputation WHERE npc_guid = 2723 AND player_guid = 710")
            cur.execute("DELETE FROM player_quest_progress WHERE player_guid = 710 AND quest_id = 'wolves_goldshire'")
            print("✅ Тестовые данные удалены")
    except Exception as e:
        print(f"⚠️ Ошибка при очистке: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    print("\n🔧 ДИАГНОСТИКА БАЗЫ ДАННЫХ")
    print("Проверяем, можем ли мы писать в таблицы вручную\n")
    
    test_memory_write()
    test_reputation_write()
    test_quest_give()
    
    print("\n" + "═" * 60)
    print("Хотите удалить тестовые данные? (y/n): ", end="")
    answer = input().strip().lower()
    if answer == 'y':
        cleanup_test_data()
    else:
        print("⚠️ Тестовые данные ОСТАВЛЕНЫ в базе!")
        print("   npc_guid=9999, player_guid=710 — удалите вручную если нужно")
    
    print("\n✅ Диагностика завершена")
"""
debug_npc_state.py — Отладочный скрипт для проверки состояния NPC-системы

Запуск: python debug_npc_state.py

Показывает:
  • Историю диалогов (npc_memory)
  • Репутацию игроков (npc_reputation)
  • Прогресс квестов (player_quest_progress)
  • Статистику по всей системе
"""

import pymysql
import sys
from datetime import datetime

# ─── КОНФИГУРАЦИЯ (берём из config.py если есть, иначе ручная) ───
try:
    import config
    DB_CONFIG = {
        "host": config.MYSQL_HOST,
        "port": config.MYSQL_PORT,
        "user": config.MYSQL_USER,
        "password": config.MYSQL_PASSWORD,
        "database": config.MYSQL_DB_CHARACTERS,
        "charset": "utf8mb4",
    }
except ImportError:
    # Ручная настройка (если config.py недоступен)
    DB_CONFIG = {
        "host": "127.0.0.1",
        "port": 3306,
        "user": "acore",
        "password": "acore",
        "database": "acore_characters",
        "charset": "utf8mb4",
    }


def get_connection():
    """Создать соединение с MySQL."""
    return pymysql.connect(**DB_CONFIG)


def print_header(title):
    """Красивый заголовок раздела."""
    print("\n" + "═" * 70)
    print(f"  {title}")
    print("═" * 70)


def format_timestamp(ts):
    """Преобразовать UNIX timestamp в читаемую дату."""
    if not ts or ts == 0:
        return "никогда"
    try:
        return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M:%S")
    except:
        return str(ts)


def check_npc_memory(limit=10):
    """
    Показать историю диалогов из npc_memory.
    
    Это ДОЛГОСРОЧНАЯ память NPC — записи остаются навсегда (пока не удалите).
    Каждый раз, когда NPC отвечает игроку — сюда пишется запись.
    """
    print_header("🧠 ДОЛГОСРОЧНАЯ ПАМЯТЬ NPC (таблица npc_memory)")
    
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT npc_guid, npc_entry, player_guid, player_name,
                       memory_type, content, player_message, npc_response,
                       mood_after, reputation_after, created_at
                FROM npc_memory
                ORDER BY created_at DESC
                LIMIT %s
            """, (limit,))
            
            rows = cur.fetchall()
            
            if not rows:
                print("  ⚠️  ТАБЛИЦА ПУСТА! Диалоги не сохраняются в память.")
                print("      Возможные причины:")
                print("      • handlers.py не вызывает db.save_memory()")
                print("      • Ошибка в _update_entity_state()")
                print("      • Таблица npc_memory не создана (запустите schema_npc_v1.sql)")
                return False
            
            print(f"  📊 Найдено записей: {len(rows)} (показаны последние {limit})")
            print("-" * 70)
            
            for i, row in enumerate(rows, 1):
                npc_guid, npc_entry, p_guid, p_name, mem_type, content, p_msg, npc_reply, mood, rep, created = row
                
                print(f"\n  [{i}] 🕐 {format_timestamp(created)}")
                print(f"      👤 Игрок: {p_name} (GUID: {p_guid})")
                print(f"      🤖 NPC: GUID={npc_guid}, Entry={npc_entry}")
                print(f"      💭 Тип памяти: {mem_type}")
                print(f"      😊 Настроение после: {mood} | Репутация: {rep}")
                print(f"      📝 Игрок сказал: {p_msg[:80] if p_msg else '—'}")
                print(f"      💬 NPC ответил: {npc_reply[:80] if npc_reply else '—'}")
                print(f"      📄 Полный контент: {content[:100]}...")
            
            # Общая статистика
            cur.execute("SELECT COUNT(*) FROM npc_memory")
            total = cur.fetchone()[0]
            print(f"\n  📈 ВСЕГО записей в памяти: {total}")
            
            # Уникальные NPC
            cur.execute("SELECT COUNT(DISTINCT npc_guid) FROM npc_memory")
            unique_npcs = cur.fetchone()[0]
            print(f"  📈 Уникальных NPC: {unique_npcs}")
            
            # Уникальные игроки
            cur.execute("SELECT COUNT(DISTINCT player_guid) FROM npc_memory")
            unique_players = cur.fetchone()[0]
            print(f"  📈 Уникальных игроков: {unique_players}")
            
            return True
            
    except Exception as e:
        print(f"  ❌ ОШИБКА: {e}")
        return False
    finally:
        conn.close()


def check_npc_reputation():
    """
    Показать репутацию игроков у NPC.
    
    Репутация меняется после каждого диалога (если mood_change значительный).
    Смотрите колонку total_dialogues — она должна расти.
    """
    print_header("⭐ РЕПУТАЦИЯ ИГРОКОВ (таблица npc_reputation)")
    
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT npc_guid, npc_entry, player_guid, player_name,
                       reputation, reputation_rank, total_dialogues,
                       quests_given, quests_completed, last_interaction_at
                FROM npc_reputation
                ORDER BY last_interaction_at DESC
            """)
            
            rows = cur.fetchall()
            
            if not rows:
                print("  ⚠️  ТАБЛИЦА ПУСТА! Репутация не записывается.")
                print("      Возможные причины:")
                print("      • handlers.py не вызывает db.update_reputation()")
                print("      • Ошибка в _update_entity_state()")
                print("      • Таблица npc_reputation не создана")
                return False
            
            print(f"  📊 Найдено записей: {len(rows)}")
            print("-" * 70)
            
            for row in rows:
                npc_guid, npc_entry, p_guid, p_name, rep, rank, dialogs, quests_g, quests_c, last_at = row
                
                # Цветовая индикация репутации
                rep_color = ""
                if rep >= 50:
                    rep_indicator = "🟢"
                elif rep >= 0:
                    rep_indicator = "⚪"
                elif rep >= -50:
                    rep_indicator = "🟡"
                else:
                    rep_indicator = "🔴"
                
                print(f"\n  {rep_indicator} Игрок '{p_name}' (GUID: {p_guid})")
                print(f"      🤖 У NPC: GUID={npc_guid}, Entry={npc_entry}")
                print(f"      📊 Репутация: {rep} ({rank})")
                print(f"      💬 Всего диалогов: {dialogs}")
                print(f"      📋 Квестов выдано: {quests_g} | Выполнено: {quests_c}")
                print(f"      🕐 Последнее взаимодействие: {format_timestamp(last_at)}")
            
            return True
            
    except Exception as e:
        print(f"  ❌ ОШИБКА: {e}")
        return False
    finally:
        conn.close()


def check_quests():
    """
    Показать состояние квестовой системы.
    
    Проверяем: есть ли определения квестов и прогресс игроков.
    """
    print_header("📋 КВЕСТОВАЯ СИСТЕМА")
    
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Сначала — доступные квесты
            print("\n  📜 ДОСТУПНЫЕ КВЕСТЫ (npc_quests):")
            cur.execute("""
                SELECT quest_id, quest_name, giver_npc_entry, giver_npc_name,
                       required_item_count, required_npc_kills, reward_gold, reward_reputation
                FROM npc_quests
            """)
            
            quest_rows = cur.fetchall()
            if not quest_rows:
                print("      ⚠️  Нет определённых квестов!")
            else:
                for row in quest_rows:
                    qid, qname, giver_entry, giver_name, items, kills, gold, rep = row
                    print(f"      • '{qname}' (ID: {qid})")
                    print(f"        Выдаёт: {giver_name} (entry {giver_entry})")
                    print(f"        Цель: собрать {items} предметов, убить {kills} NPC")
                    print(f"        Награда: {gold} меди, {rep} репутации")
            
            # Прогресс игроков
            print("\n  👤 ПРОГРЕСС ИГРОКОВ (player_quest_progress):")
            cur.execute("""
                SELECT player_guid, player_name, quest_id, status,
                       item_count, npc_kills, given_at, completed_at
                FROM player_quest_progress
                ORDER BY given_at DESC
            """)
            
            progress_rows = cur.fetchall()
            if not progress_rows:
                print("      ⚠️  Нет активных квестов у игроков!")
                print("      💡 Чтобы выдать квест — используйте:")
                print("         POST /admin/quest/wolves_goldshire/give")
                print("         с JSON: {\"player_guid\": 123, \"player_name\": \"ВашИгрок\"}")
            else:
                for row in progress_rows:
                    p_guid, p_name, qid, status, items, kills, given, completed = row
                    status_emoji = "✅" if status == "completed" else "🔄" if status == "active" else "⏸️"
                    print(f"      {status_emoji} Игрок '{p_name}' — квест '{qid}'")
                    print(f"         Статус: {status}")
                    print(f"         Прогресс: {items} предметов, {kills} убийств")
                    print(f"         Выдан: {format_timestamp(given)}")
                    if completed:
                        print(f"         Завершён: {format_timestamp(completed)}")
            
            return True
            
    except Exception as e:
        print(f"  ❌ ОШИБКА: {e}")
        return False
    finally:
        conn.close()


def check_ai_requests_responses():
    """
    Показать последние запросы и ответы (сырой обмен Lua ↔ Python).
    
    Это «низкий уровень» — проверяем, ходят ли данные через таблицы.
    """
    print_header("🔄 ОБМЕН ДАННЫМИ Lua ↔ Python (ai_requests / ai_responses)")
    
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Последние запросы
            print("\n  📥 ПОСЛЕДНИЕ ЗАПРОСЫ (ai_requests):")
            cur.execute("""
                SELECT id, player_name, npc_name, message, channel_type, created_at
                FROM ai_requests
                ORDER BY id DESC
                LIMIT 5
            """)
            
            req_rows = cur.fetchall()
            if not req_rows:
                print("      ⚠️  Нет запросов! Lua не пишет в таблицу.")
            else:
                for row in req_rows:
                    rid, p_name, n_name, msg, ch, created = row
                    print(f"      [{rid}] {p_name} -> {n_name} [{ch}]")
                    print(f"          '{msg[:60]}...' ({format_timestamp(created)})")
            
            # Последние ответы
            print("\n  📤 ПОСЛЕДНИЕ ОТВЕТЫ (ai_responses):")
            cur.execute("""
                SELECT id, player_guid, npc_guid, response_text, emote_id, fetched, created_at
                FROM ai_responses
                ORDER BY id DESC
                LIMIT 5
            """)
            
            resp_rows = cur.fetchall()
            if not resp_rows:
                print("      ⚠️  Нет ответов! Python не пишет в таблицу.")
            else:
                for row in resp_rows:
                    rid, p_guid, n_guid, text, emote, fetched, created = row
                    status = "✅ доставлено" if fetched else "⏳ ожидает"
                    print(f"      [{rid}] NPC {n_guid} -> Игрок {p_guid} [{status}]")
                    print(f"          '{text[:60]}...' (эмоция: {emote})")
            
            return True
            
    except Exception as e:
        print(f"  ❌ ОШИБКА: {e}")
        return False
    finally:
        conn.close()


def main():
    """
    Главная функция — запускает все проверки подряд.
    """
    print("\n" + "╔" + "═" * 68 + "╗")
    print("║" + " " * 15 + "🔍 ОТЛАДКА СОСТОЯНИЯ LivingAzeroth" + " " * 18 + "║")
    print("║" + " " * 20 + "Проверка памяти, репутации, квестов" + " " * 13 + "║")
    print("╚" + "═" * 68 + "╝")
    
    print(f"\n  📡 Подключение к: {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}")
    print(f"  👤 Пользователь: {DB_CONFIG['user']}")
    
    # Проверяем соединение
    try:
        conn = get_connection()
        conn.close()
        print("  ✅ Соединение с MySQL: ОК")
    except Exception as e:
        print(f"  ❌ Не могу подключиться к MySQL: {e}")
        print("      Проверьте: запущен ли сервер, правильные ли логин/пароль")
        sys.exit(1)
    
    # Запускаем все проверки
    results = []
    
    results.append(("Обмен Lua↔Python", check_ai_requests_responses()))
    results.append(("Память NPC", check_npc_memory()))
    results.append(("Репутация", check_npc_reputation()))
    results.append(("Квесты", check_quests()))
    
    # Итог
    print_header("📊 ИТОГОВЫЙ ОТЧЁТ")
    
    all_ok = True
    for name, ok in results:
        status = "✅ РАБОТАЕТ" if ok else "❌ ПРОБЛЕМА"
        print(f"  {status} — {name}")
        if not ok:
            all_ok = False
    
    print("\n" + "═" * 70)
    if all_ok:
        print("  🎉 ВСЕ СИСТЕМЫ РАБОТАЮТ! Можно переходить к расширению.")
    else:
        print("  ⚠️  ЕСТЬ ПРОБЛЕМЫ. Смотрите описание выше.")
        print("      Скорее всего — таблицы не созданы или handlers.py")
        print("      не вызывает методы сохранения.")
    print("═" * 70 + "\n")


if __name__ == "__main__":
    main()
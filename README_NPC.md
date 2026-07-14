# LivingAzeroth — Вариант А: Живые NPC

## Что изменилось

- ❌ Убрана вся тактическая система (боты, планы, команды)
- ✅ Фокус на диалогах с NPC: память, репутация, квесты
- ✅ Жёсткая персонализация NPC по entry ID
- ✅ Долгосрочная память в MySQL (переживает рестарт)

## Быстрый старт

### 1. Останови сервер AzerothCore

### 2. Замени файлы

```
# Lua скрипты (в папке сервера lua_scripts/)
AI_World.lua          → новая версия (v4.0 NPC-only)
AI_Tactics.lua        → УДАЛИТЬ (не нужен)

# Python (в папке проекта)
modules/creature_ai/handlers.py    → новая версия
modules/creature_ai/prompts.py     → новая версия
wow_connector/db_bridge.py         → новая версия
config.py                          → новая версия
```

### 3. Выполни SQL в базе `acore_characters`

```bash
mysql -u acore -p acore_characters < database/schema_npc_v1.sql
```

### 4. Запусти сервер

```bash
# 1. Запусти LM Studio с моделью
# 2. Запусти worldserver ( AzerothCore )
# 3. Запусти Python:
python main.py
```

### 5. Проверь в игре

Подойди к **Штормградскому стражнику** в Голдшире и скажи:
- `Привет` — NPC ответит с учётом времени и погоды
- `Расскажи о волках` — упомянет квест
- `Я согласен помочь` — получишь квест (запишется в БД)

## Архитектура

```
Игрок говорит (SAY)
    ↓
AI_World.lua → ai_requests
    ↓ (500ms)
WoWDBBridge → EventBus
    ↓
CreatureAIHandler
    ├── Загружает профиль NPC (по entry ID)
    ├── Загружает память из MySQL
    ├── Загружает репутацию
    ├── Собирает промпт для LLM
    └── Отправляет в LLM Queue
    ↓
LLM (LM Studio) → JSON ответ
    ↓
validators → ai_responses
    ↓ (500ms polling)
AI_World.lua → NPC говорит в игре
```

## Профили NPC

Жёстко заданы в `handlers.py` → `_get_npc_profile()`:

| Entry ID | NPC | Роль | Что знает |
|----------|-----|------|-----------|
| 68 | Штормградский стражник | Стражник | Волки, бандиты, дорога в Штормград |
| 1756 | Штормградский стражник | Стражник | То же самое |

Добавляй новые профили в словарь `HARDCODED_PROFILES`.

## Квесты

Определены в таблице `npc_quests`:

| ID | Название | Требование | Награда |
|----|----------|------------|---------|
| wolves_goldshire | Волки у Голдшира | 3 Wolf Hide | 5 серебра, +10 репутации |

## Таблицы БД

| Таблица | Назначение |
|---------|-----------|
| `ai_requests` | Входящие сообщения от игроков |
| `ai_responses` | Исходящие ответы NPC |
| `npc_memory` | История диалогов (долгосрочная) |
| `npc_reputation` | Репутация игроков у NPC |
| `npc_quests` | Определения квестов |
| `player_quest_progress` | Прогресс игроков |

## Отладка

Проверь логи Python:
```bash
tail -f logs/living_azeroth.log
```

Проверь что Lua загрузился:
```
# В консоли worldserver
reload eluna
```

Проверь таблицы:
```sql
SELECT * FROM npc_memory ORDER BY created_at DESC LIMIT 5;
SELECT * FROM npc_reputation WHERE player_guid = ТВОЙ_GUID;
```

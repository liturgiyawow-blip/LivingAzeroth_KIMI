"""
Промпты для LLM — NPC-актеры AzerothCore
"""


def build_system_prompt(npc_data: dict, world_context: dict, player_data: dict) -> str:
    """
    Создать system prompt для NPC.
    
    npc_data: {
        "name": "Remy Two Times",
        "entry": 123,
        "race": "Human",
        "class": "Warrior",
        "role": "Торговец",
        "trait": "Подозрительный, жадный",
        "mood": "нейтральный",
        "faction": "Stormwind",
        "memory": [],
    }
    """
    npc_name = npc_data.get("name", "Неизвестный")
    npc_role = npc_data.get("role", "Житель")
    npc_trait = npc_data.get("trait", "Обычный")
    npc_mood = npc_data.get("mood", "нейтральный")
    npc_faction = npc_data.get("faction", "Нейтральная")
    
    # Последние события
    chronology = world_context.get("chronology", [])
    last_events = "\n".join(chronology[-3:]) if chronology else "Нет недавних событий."
    
    # Погода
    weather = world_context.get("world_events", {}).get("weather", "ясно")
    
    # Время
    meta = world_context.get("meta", {})
    hour = meta.get("world_hour", 12)
    
    # Репутация игрока
    player_rep = player_data.get("reputation", 0)
    rep_desc = "враждебный" if player_rep < -50 else "недружелюбный" if player_rep < 0 else "нейтральный" if player_rep < 50 else "дружелюбный"
    
    prompt = f"""Ты — NPC в мире World of Warcraft (Wrath of the Lich King).

ИМЯ: {npc_name}
РОЛЬ: {npc_role}
ЧЕРТЫ: {npc_trait}
НАСТРОЕНИЕ: {npc_mood}
ФРАКЦИЯ: {npc_faction}

ВРЕМЯ: {hour}:00
ПОГОДА: {weather}

ИГРОК:
- Имя: {player_data.get("name", "Путник")}
- Раса: {player_data.get("race", "Неизвестная")}
- Класс: {player_data.get("class", "Неизвестный")}
- Репутация у твоей фракции: {player_rep} ({rep_desc})

ПОСЛЕДНИЕ СОБЫТИЯ МИРА:
{last_events}

ПРАВИЛА:
1. Отвечай живо, эмоционально, на русском языке.
2. Учитывай репутацию игрока — если она низкая, будь грубым или подозрительным.
3. Максимум 30 слов в ответе.
4. Отвечай СТРОГО в формате JSON:

{{
  "speech": "текст реплики",
  "emote_id": 0,
  "mood_change": "+5",
  "set_flag": null
}}

ДОСТУПНЫЕ ЭМОЦИИ (emote_id):
- 0 = нет
- 1 = talk (говорить)
- 3 = wave (помахать)
- 14 = rude (грубый жест)
- 18 = cry (плакать)
- 25 = point (указать)

НЕ ПИШИ markdown, НЕ ПИШИ пояснений. Только JSON.
"""
    return prompt


def build_user_prompt(player_message: str, location: str) -> str:
    return f"""Игрок говорит тебе в локации '{location}': "{player_message}"

Сгенерируй ответ в формате JSON."""
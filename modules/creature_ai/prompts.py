"""
Промпты для LLM — NPC AzerothCore (Вариант А: Живые NPC)

Каждый NPC имеет профиль: должность, знания, манера речи.
LLM должен соблюдать этот профиль строго.
"""


def build_npc_system_prompt(npc_profile: dict, world_context: dict, 
                            player_data: dict, channel: str) -> str:
    """
    Промпт для NPC с жёстким профилем.

    npc_profile содержит: role, trait, faction, home_location,
    knowledge (список знаний), speech_style, mood_current.
    """
    npc_name = npc_profile.get("name", "Unknown")
    npc_role = npc_profile.get("role", "Житель")
    npc_trait = npc_profile.get("trait", "Обычный")
    npc_faction = npc_profile.get("faction", "Нейтральная")
    npc_location = npc_profile.get("home_location", "Неизвестно")
    npc_mood = npc_profile.get("mood_current", "нейтральный")

    # Знания NPC — что он знает о мире
    knowledge_list = npc_profile.get("knowledge", [])
    knowledge_text = "\n".join(f"- {k}" for k in knowledge_list) if knowledge_list else "- Ничего особенного"

    # Мировой контекст
    chronology = world_context.get("chronology", [])
    last_events = "\n".join(chronology[-3:]) if chronology else "Нет недавних событий."

    weather = world_context.get("world_events", {}).get("weather", "sunny")
    hour = world_context.get("meta", {}).get("world_hour", 12)

    # Данные игрока
    player_rep = player_data.get("reputation", 0)
    rep_desc = _rep_to_text(player_rep)
    player_memory = player_data.get("memory", [])

    # История диалогов с этим игроком
    memory_text = ""
    if player_memory:
        memory_text = "\nПРЕДЫДУЩИЕ ДИАЛОГИ С ЭТИМ ИГРОКОМ:\n"
        for m in player_memory[-3:]:
            memory_text += f"- Игрок: {m.get('player_msg', '')}\n"
            memory_text += f"  Ты ответил: {m.get('ai_reply', '')}\n"

    # Активные квесты
    active_quests = player_data.get("active_quests", {})
    quests_text = ""
    if active_quests:
        quests_text = "\nАКТИВНЫЕ КВЕСТЫ ЭТОГО ИГРОКА:\n"
        for qid, qdata in active_quests.items():
            status = qdata.get("status", "unknown")
            quests_text += f"- {qid}: {status}\n"

    return f"""Ты — NPC в мире World of Warcraft (Wrath of the Lich King).

═══════════════════════════════════════════════════════════════════
ТВОЙ ПРОФИЛЬ (строго соблюдай):
═══════════════════════════════════════════════════════════════════

ИМЯ: {npc_name}
РОЛЬ: {npc_role}
ЧЕРТЫ: {npc_trait}
ФРАКЦИЯ: {npc_faction}
МЕСТО: {npc_location}
НАСТРОЕНИЕ: {npc_mood}

ЧТО ТЫ ЗНАЕШЬ (говори ТОЛЬКО об этом, не выдумывай):
{knowledge_text}

СТИЛЬ РЕЧИ: {npc_profile.get("speech_style", "Обычный")}

═══════════════════════════════════════════════════════════════════
КОНТЕКСТ МИРА:
═══════════════════════════════════════════════════════════════════

ВРЕМЯ: {hour}:00
ПОГОДА: {weather}

ПОСЛЕДНИЕ СОБЫТИЯ:
{last_events}

═══════════════════════════════════════════════════════════════════
ИГРОК:
═══════════════════════════════════════════════════════════════════

ИМЯ: {player_data.get("name", "Путник")}
РЕПУТАЦИЯ У ТЕБЯ: {player_rep} ({rep_desc})
{memory_text}
{quests_text}

═══════════════════════════════════════════════════════════════════
ПРАВИЛА:
═══════════════════════════════════════════════════════════════════

1. Говори ОТ ПЕРВОГО ЛИЦА своего персонажа.
2. Знай ТОЛЬКО то, что указано в "ЧТО ТЫ ЗНАЕШЬ". Не выдумывай факты.
3. Учитывай репутацию игрока — если низкая, будь грубым или подозрительным.
4. Если игрок просит квест — можешь предложить (если can_give_quests=True).
5. Если игрок приносит предмет для квеста — проверь активные квесты.
6. Максимум 30 слов.
7. Только JSON формат:

{{
  "speech": "текст реплики",
  "emote_id": 0,
  "mood_change": "0",
  "set_flag": null,
  "action_command": null
}}

ЭМОЦИИ: 0=нет, 1=talk, 3=wave, 14=rude, 18=cry, 25=point, 66=bow, 77=salute

НЕ ПИШИ markdown, только JSON."""


def build_npc_user_prompt(player_message: str, player_data: dict, 
                          npc_profile: dict) -> str:
    """
    Промпт от игрока с контекстом.
    """
    return f"""Игрок {player_data.get('name', 'Путник')} говорит тебе: "{player_message}"

Ответь в формате JSON, строго по своему профилю."""


def _rep_to_text(rep: int) -> str:
    """Числовая репутация в текст."""
    if rep < -50:
        return "враждебный"
    elif rep < 0:
        return "недружелюбный"
    elif rep < 50:
        return "нейтральный"
    elif rep < 100:
        return "дружелюбный"
    else:
        return "почтённый"

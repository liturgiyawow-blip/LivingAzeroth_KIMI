"""
Валидаторы ответов NPC для AzerothCore
"""


VALID_EMOTES = {0, 1, 3, 14, 18, 25}
MAX_SPEECH_LENGTH = 255  # лимит WoW Say


def validate_response(decision: dict, npc_name: str) -> dict:
    if not isinstance(decision, dict):
        return _fallback_response(npc_name)
    
    # Речь
    speech = decision.get("speech", "")
    if not isinstance(speech, str):
        speech = str(speech)
    
    # Обрезать до 255 символов
    if len(speech) > MAX_SPEECH_LENGTH:
        speech = speech[:252] + "..."
    
    # Эмоция
    emote = decision.get("emote_id", 0)
    try:
        emote = int(emote)
    except (ValueError, TypeError):
        emote = 0
    if emote not in VALID_EMOTES:
        emote = 0
    
    # Настроение
    mood = decision.get("mood_change", "0")
    try:
        mood_val = int(mood)
        mood = str(max(-50, min(50, mood_val)))
    except (ValueError, TypeError):
        mood = "0"
    
    # Флаг / команда действия
    flag = decision.get("set_flag")
    if flag is not None and not isinstance(flag, str):
        flag = str(flag)[:50]
    
    action_cmd = decision.get("action_command")
    if action_cmd is not None and not isinstance(action_cmd, str):
        action_cmd = str(action_cmd)[:50]
    
    return {
        "speech": speech,
        "emote_id": emote,
        "mood_change": mood,
        "set_flag": flag,
        "action_command": action_cmd,
    }


def _fallback_response(npc_name: str) -> dict:
    return {
        "speech": f"{npc_name} смотрит на вас молча.",
        "emote_id": 0,
        "mood_change": "0",
        "set_flag": None,
        "action_command": None,
    }
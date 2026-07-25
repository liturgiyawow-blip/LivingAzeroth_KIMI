"""
Voice Map — сопоставление (race, gender) → голосовой референс
При переходе F5-TTS → GPT-SoVITS меняется только content_type и path
"""

import config
from pathlib import Path

# F5-TTS: указываем путь к .wav референсу (10-15 сек)
# GPT-SoVITS: указываем model_name (базовая модель + весы подгружаются по имени)
VOICE_MAP = {
    ("Human", "Male"):    {"id": "human_male",    "file": "human_male.wav"},
    ("Human", "Female"):  {"id": "human_female",  "file": "human_female.wav"},
    ("Orc", "Male"):      {"id": "orc_male",      "file": "orc_male.wav"},
    ("Orc", "Female"):    {"id": "orc_female",    "file": "orc_female.wav"},
    ("Dwarf", "Male"):    {"id": "dwarf_male",    "file": "dwarf_male.wav"},
    ("Dwarf", "Female"):  {"id": "dwarf_female",  "file": "dwarf_female.wav"},
    ("Night Elf", "Male"):   {"id": "nelf_male",    "file": "nelf_male.wav"},
    ("Night Elf", "Female"): {"id": "nelf_female",  "file": "nelf_female.wav"},
    ("Undead", "Male"):      {"id": "undead_male",    "file": "undead_male.wav"},
    ("Undead", "Female"):    {"id": "undead_female",  "file": "undead_female.wav"},
    ("Tauren", "Male"):      {"id": "tauren_male",    "file": "tauren_male.wav"},
    ("Tauren", "Female"):    {"id": "tauren_female",  "file": "tauren_female.wav"},
    ("Troll", "Male"):       {"id": "troll_male",     "file": "troll_male.wav"},
    ("Troll", "Female"):     {"id": "troll_female",   "file": "troll_female.wav"},
    ("Gnome", "Male"):       {"id": "gnome_male",     "file": "gnome_male.wav"},
    ("Gnome", "Female"):     {"id": "gnome_female",   "file": "gnome_female.wav"},
    ("Blood Elf", "Male"):   {"id": "belf_male",      "file": "belf_male.wav"},
    ("Blood Elf", "Female"): {"id": "belf_female",    "file": "belf_female.wav"},
    ("Draenei", "Male"):     {"id": "draenei_male",   "file": "draenei_male.wav"},
    ("Draenei", "Female"):   {"id": "draenei_female", "file": "draenei_female.wav"},
}

def get_voice_path(race: str, gender: str) -> Path:
    """Вернуть абсолютный путь к референсу."""
    entry = VOICE_MAP.get((race, gender))
    if not entry:
        # Fallback: Human Male если голос не найден
        entry = VOICE_MAP[("Human", "Male")]
    return config.TTS_REF_DIR / entry["file"]

def get_voice_id(race: str, gender: str) -> str:
    """Вернуть voice_id (для GPT-SoVITS это model_name)."""
    entry = VOICE_MAP.get((race, gender))
    if not entry:
        return "human_male"
    return entry["id"]
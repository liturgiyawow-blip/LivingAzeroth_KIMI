import config
from pathlib import Path

VOICE_MAP = {
    ("Human", "Male"):    {"id": "human_male",    "file": "human_male.wav",    "ref_text": "неужели такие красавицы бывают наяву? Не-е-ет, я точно еще не проснулся! Иначе почему меня так качает?"},
    ("Human", "Female"):  {"id": "human_female",  "file": "human_female.wav",  "ref_text": "неужели такие красавицы бывают наяву? Не-е-ет, я точно еще не проснулся! Иначе почему меня так качает?"},
    ("Orc", "Male"):      {"id": "orc_male",      "file": "orc_male.wav",      "ref_text": "неужели такие красавицы бывают наяву? Не-е-ет, я точно еще не проснулся! Иначе почему меня так качает?"},
    ("Orc", "Female"):    {"id": "orc_female",    "file": "orc_female.wav",    "ref_text": "неужели такие красавицы бывают наяву? Не-е-ет, я точно еще не проснулся! Иначе почему меня так качает?"},
    ("Dwarf", "Male"):    {"id": "dwarf_male",    "file": "dwarf_male.wav",    "ref_text": "неужели такие красавицы бывают наяву? Не-е-ет, я точно еще не проснулся! Иначе почему меня так качает?"},
    ("Dwarf", "Female"):  {"id": "dwarf_female",  "file": "dwarf_female.wav",  "ref_text": "неужели такие красавицы бывают наяву? Не-е-ет, я точно еще не проснулся! Иначе почему меня так качает?"},
    ("Night Elf", "Male"):   {"id": "nelf_male",    "file": "nelf_male.wav",    "ref_text": "неужели такие красавицы бывают наяву? Не-е-ет, я точно еще не проснулся! Иначе почему меня так качает?"},
    ("Night Elf", "Female"): {"id": "nelf_female",  "file": "nelf_female.wav",  "ref_text": "неужели такие красавицы бывают наяву? Не-е-ет, я точно еще не проснулся! Иначе почему меня так качает?"},
    ("Undead", "Male"):      {"id": "undead_male",    "file": "undead_male.wav",    "ref_text": "неужели такие красавицы бывают наяву? Не-е-ет, я точно еще не проснулся! Иначе почему меня так качает?"},
    ("Undead", "Female"):    {"id": "undead_female",  "file": "undead_female.wav",  "ref_text": "неужели такие красавицы бывают наяву? Не-е-ет, я точно еще не проснулся! Иначе почему меня так качает?"},
    ("Tauren", "Male"):      {"id": "tauren_male",    "file": "tauren_male.wav",    "ref_text": "неужели такие красавицы бывают наяву? Не-е-ет, я точно еще не проснулся! Иначе почему меня так качает?"},
    ("Tauren", "Female"):    {"id": "tauren_female",  "file": "tauren_female.wav",  "ref_text": "неужели такие красавицы бывают наяву? Не-е-ет, я точно еще не проснулся! Иначе почему меня так качает?"},
    ("Troll", "Male"):       {"id": "troll_male",     "file": "troll_male.wav",     "ref_text": "неужели такие красавицы бывают наяву? Не-е-ет, я точно еще не проснулся! Иначе почему меня так качает?"},
    ("Troll", "Female"):     {"id": "troll_female",   "file": "troll_female.wav",   "ref_text": "неужели такие красавицы бывают наяву? Не-е-ет, я точно еще не проснулся! Иначе почему меня так качает?"},
    ("Gnome", "Male"):       {"id": "gnome_male",     "file": "gnome_male.wav",     "ref_text": "неужели такие красавицы бывают наяву? Не-е-ет, я точно еще не проснулся! Иначе почему меня так качает?"},
    ("Gnome", "Female"):     {"id": "gnome_female",   "file": "gnome_female.wav",   "ref_text": "неужели такие красавицы бывают наяву? Не-е-ет, я точно еще не проснулся! Иначе почему меня так качает?"},
    ("Blood Elf", "Male"):   {"id": "belf_male",      "file": "belf_male.wav",      "ref_text": "неужели такие красавицы бывают наяву? Не-е-ет, я точно еще не проснулся! Иначе почему меня так качает?"},
    ("Blood Elf", "Female"): {"id": "belf_female",    "file": "belf_female.wav",    "ref_text": "неужели такие красавицы бывают наяву? Не-е-ет, я точно еще не проснулся! Иначе почему меня так качает?"},
    ("Draenei", "Male"):     {"id": "draenei_male",   "file": "draenei_male.wav",   "ref_text": "неужели такие красавицы бывают наяву? Не-е-ет, я точно еще не проснулся! Иначе почему меня так качает?"},
    ("Draenei", "Female"):   {"id": "draenei_female", "file": "draenei_female.wav", "ref_text": "неужели такие красавицы бывают наяву? Не-е-ет, я точно еще не проснулся! Иначе почему меня так качает?"},
}

def get_voice_path(race: str, gender: str) -> Path:
    entry = VOICE_MAP.get((race, gender))
    if not entry:
        entry = VOICE_MAP[("Human", "Male")]
    return config.TTS_REF_DIR / entry["file"]

def get_voice_id(race: str, gender: str) -> str:
    entry = VOICE_MAP.get((race, gender))
    if not entry:
        return "human_male"
    return entry["id"]

def get_ref_text(race: str, gender: str) -> str:
    entry = VOICE_MAP.get((race, gender))
    if not entry:
        entry = VOICE_MAP[("Human", "Male")]
    return entry["ref_text"]
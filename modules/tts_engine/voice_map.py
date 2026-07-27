import config
from pathlib import Path

VOICE_MAP = {
    ("Human", "Male"):    {"id": "human_male",    "file": "human_male.wav",    "ref_text": "Все мечтают хорошо провести время... Но время не проведешь!"},
    ("Human", "Female"):  {"id": "human_female",  "file": "human_female.wav",  "ref_text": "А вы никогда не чувствовали, будто совсем не управляете своей судьбой? Словно бы вас ведет невидимая рука!"},
    ("Orc", "Male"):      {"id": "orc_male",      "file": "orc_male.wav",      "ref_text": "неужели такие красавицы бывают наяву? Не-е-ет, я точно еще не проснулся! Иначе почему меня так качает?"},
    ("Orc", "Female"):    {"id": "orc_female",    "file": "orc_female.wav",    "ref_text": "неужели такие красавицы бывают наяву? Не-е-ет, я точно еще не проснулся! Иначе почему меня так качает?"},
    ("Dwarf", "Male"):    {"id": "dwarf_male",    "file": "dwarf_male.wav",    "ref_text": "неужели такие красавицы бывают наяву? Не-е-ет, я точно еще не проснулся! Иначе почему меня так качает?"},
    ("Dwarf", "Female"):  {"id": "dwarf_female",  "file": "dwarf_female.wav",  "ref_text": "А я себе каждый вечер педикюр в печке голландке делаю! Грибкаа как не бывало, а пальчики! просто безупречны."},
    ("Night Elf", "Male"):   {"id": "nelf_male",    "file": "nelf_male.wav",    "ref_text": "Не знаю как насчет тебя, но лично я не понимаю ни слова из того что говорят эти светлячки. Обычно я прост киваю в ответ."},
    ("Night Elf", "Female"): {"id": "nelf_female",  "file": "nelf_female.wav",  "ref_text": "По моему, изумрудный сон - это всего лишь отговорка, чтобы больше не встречаться со мной!"},
    ("Undead", "Male"):      {"id": "undead_male",    "file": "undead_male.wav",    "ref_text": "неужели такие красавицы бывают наяву? Не-е-ет, я точно еще не проснулся! Иначе почему меня так качает?"},
    ("Undead", "Female"):    {"id": "undead_female",  "file": "undead_female.wav",  "ref_text": "неужели такие красавицы бывают наяву? Не-е-ет, я точно еще не проснулся! Иначе почему меня так качает?"},
    ("Tauren", "Male"):      {"id": "tauren_male",    "file": "tauren_male.wav",    "ref_text": "неужели такие красавицы бывают наяву? Не-е-ет, я точно еще не проснулся! Иначе почему меня так качает?"},
    ("Tauren", "Female"):    {"id": "tauren_female",  "file": "tauren_female.wav",  "ref_text": "неужели такие красавицы бывают наяву? Не-е-ет, я точно еще не проснулся! Иначе почему меня так качает?"},
    ("Troll", "Male"):       {"id": "troll_male",     "file": "troll_male.wav",     "ref_text": "неужели такие красавицы бывают наяву? Не-е-ет, я точно еще не проснулся! Иначе почему меня так качает?"},
    ("Troll", "Female"):     {"id": "troll_female",   "file": "troll_female.wav",   "ref_text": "неужели такие красавицы бывают наяву? Не-е-ет, я точно еще не проснулся! Иначе почему меня так качает?"},
    ("Gnome", "Male"):       {"id": "gnome_male",     "file": "gnome_male.wav",     "ref_text": "Я тут изобрел на досуге механизм для поджаривания тонких ломтиков хлеба. Но.. в конце концов подумал, что вряд ли кому то он особенно и нужен."},
    ("Gnome", "Female"):     {"id": "gnome_female",   "file": "gnome_female.wav",   "ref_text": "Я тут выяснила, если тяжелым тупым предметом стукнуть по голове, будет весьма боольно."},
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
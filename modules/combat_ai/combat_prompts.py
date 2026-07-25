"""
combat_prompts.py — Промпты для LLM при генерации post-combat фраз

Строго РП. Никаких "процентов", "парсов", "дпс метров".
Только мир Азерота, его жители и их переживания.
"""

import json
import logging
import os
import glob
from datetime import datetime
from typing import Dict, List

# ═══════════════════════════════════════════════════════════════════
# ИМПОРТ: строители идентичности из модуля диалогов
# Тянем ТОЛЬКО нужную расу и класс, а не всю простыню из personas.json
# ═══════════════════════════════════════════════════════════════════
from modules.creature_ai.prompts import (
    _build_race_identity,
    _build_class_dogma,
    _build_living_pulse,
    _build_anti_fourth_wall,
)

logger = logging.getLogger(__name__)
prompt_logger = logging.getLogger("llm_prompts")

# ═══════════════════════════════════════════════════════════════════
# ПОДГРУЗКА LIVING PERSONA ДЛЯ COMBAT
# ═══════════════════════════════════════════════════════════════════
try:
    from modules.creature_ai.persona_loader import get_persona_loader
    _persona = get_persona_loader().get_persona("roleplayers")
    LIVING_ADDENDUM = _persona.get("system_prompt_addendum", "")
    VOICE_EXAMPLES = _persona.get("voice_examples", [])
except Exception as e:
    logger.warning("Failed to load persona for combat: %s", e)
    LIVING_ADDENDUM = ""
    VOICE_EXAMPLES = []
    
# ═══════════════════════════════════════════════════════════════════
# ЛОГИРОВАНИЕ ПРОМПТОВ ДЛЯ ОТЛАДКИ (ротация: 5 последних)
# ═══════════════════════════════════════════════════════════════════

PROMPT_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompt_logs")
os.makedirs(PROMPT_LOG_DIR, exist_ok=True)


def _cleanup_old_logs(max_files: int = 5):
    """Оставить только N последних логов. Удаляем старые."""
    try:
        files = sorted(
            glob.glob(os.path.join(PROMPT_LOG_DIR, "*.json")),
            key=os.path.getmtime,
            reverse=True
        )
        for old in files[max_files:]:
            try:
                os.remove(old)
                logger.debug("[PromptLog] Removed old log: %s", old)
            except Exception as e:
                logger.warning("[PromptLog] Failed to remove %s: %s", old, e)
    except Exception as e:
        logger.error("[PromptLog] Cleanup error: %s", e)


def _log_prompt_to_file(system_prompt: str, user_prompt: str, ctx: dict) -> str:
    """
    Сохраняет полный промпт в файл для изучения.
    Ротирует: оставляет только 5 последних файлов.
    Возвращает путь к сохранённому файлу.
    """
    # Сначала чистим старые
    _cleanup_old_logs(5)

    speaker = ctx.get("speaker_name", "unknown")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    filename = f"{timestamp}_{speaker}.json"
    filepath = os.path.join(PROMPT_LOG_DIR, filename)

    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "speaker": speaker,
        "speaker_race": ctx.get("speaker_race", "unknown"),
        "speaker_class": ctx.get("speaker_class", "unknown"),
        "duration_desc": ctx.get("duration_desc", ""),
        "duration_sec": ctx.get("duration_sec", 0),
        "duration_category": ctx.get("duration_category", "unknown"),
        "severity": ctx.get("severity", 0),
        "modifiers": ctx.get("modifiers", []),
        "triggers": ctx.get("triggers", {}),
        "casualties": ctx.get("casualties", []),
        "wounded": ctx.get("wounded", []),
        "heroes": ctx.get("heroes", []),
        "boss_name": ctx.get("boss_name"),
        "enemy_count": ctx.get("enemy_count", 0),
        "enemies_names": ctx.get("enemies_names", []),
        "participants": ctx.get("participants", []),
        "speaker_main_hand": ctx.get("speaker_main_hand", "руки"),
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
    }

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(log_entry, f, ensure_ascii=False, indent=2)
        logger.info(f"[PromptLog] Saved to {filepath}")
    except Exception as e:
        logger.error(f"[PromptLog] Failed to save prompt: {e}")

    return filepath


# ═══════════════════════════════════════════════════════════════════
# НОВОЕ v5.4: АДАПТИВНЫЕ ПРАВИЛА ПОД ДЛИТЕЛЬНОСТЬ БОЯ
# ═══════════════════════════════════════════════════════════════════

DURATION_RULES = {
    "instant": {
        "tone": "Мгновенный шок, молниеносная расправа или легкое удивление.",
        "max_length": "Не более 120 символов.",
        "style": (
            "Схватка закончилась, не успев начаться! СТРОГО ЗАПРЕЩЕНО упоминать длительность или секунды. "
            "Дай живую реакцию персонажа от первого лица: он может зевотно пробурчать, отпустить ироничную колкость, "
            "удивиться слабости противника или просто фыркнуть в сторону. "
            "Если к месту — упомяни своё оружие ({speaker_main_hand}) или противника ({enemies_text}), но сделай это естественной частью реплики. "
            "Никакого сухого отчёта. Только живые эмоции воина, мага или плута Азерота."
        ),
        "hint": "Мгновенный бой. Живая, нешаблонная реакция — ирония, бытовая мысль, вздох или быстрый победный клич.",
        "examples": "М-да... И ради этого я обнажал {speaker_main_hand}? / Ой, он что, уже всё? / Даже чашку эля поставить не успел.",
    },
    "quick": {
        "tone": "Уверенная и лёгкая победа, разгар дороги.",
        "max_length": "Не более 150 символов.",
        "style": (
            "Быстрый разгром без особых проблем. НЕ используй слово 'быстро'. "
            "Персонаж может подколоть соратника ({allies_text}), оценить качество трофеев, поправить доспехи, "
            "похвастаться своим оружием ({speaker_main_hand}) или выдать колоритную фразу своей расы/класса. "
            "Формируй реплику разнообразно: от боевого азарта и гордости за фракцию до ворчливых мыслей об усталости и грязи на сапогах."
        ),
        "hint": "Легкий бой. Полный карт-бланш на подколы, расовый колорит, похвалу оружию {speaker_main_hand} и мысли воина.",
        "examples": "Эй, {leader_name}, ты видел, как мой {speaker_main_hand} прошелся? Красота! / Мелкие пакостники. Пошли дальше, у меня ноги затекли.",
    },
    "short": {
        "tone": "Рядовая стычка в приключениях по Азероту.",
        "max_length": "Не более 200 символов.",
        "style": (
            "Стандартный бой с врагами. Спикер должен звучать как ЖИВОЙ ПЕРСОНАЖ мира Warcraft. "
            "Он может отреагировать на действия соратников ({allies_text}), ругнуться на поверженного противника ({enemies_text}), "
            "вспомнить заветы предков/Лоа/Элуны/Света, проверить лезвие своего {speaker_main_hand} или просто стряхнуть пыль. "
            "Главное — уникальность настроения в каждом ответе! От сурового воинского спокойствия до черного юмора."
        ),
        "hint": "Обычный бой. Живые эмоции, подколы, классово-расовая специфика и естественно звучащая речь.",
        "examples": "Слава Стихиям, эти {enemies_text} наконец утихли! / Хорошо сработано! Кто-нибудь видел, куда улетел мой правый кинжал?",
    },
    "medium": {
        "tone": "Жаркая, потная драка, передышка на поле боя.",
        "max_length": "Не более 250 символов.",
        "style": (
            "Ожесточённый бой! Здесь нужен искренний эмоциональный накал. "
            "Если кто-то из группы ранен ({wounded_text}) — обязательно отреагируй на это: вырази тревогу, предложи помощь или клянись защитить! "
            "Если все целы — передай тяжелое дыхание, усталость, колоритные ругательства в стиле расы (Шортов бес! Зараза! Дьявол!), "
            "жалобы на зазубрины на {speaker_main_hand} или радость от того, что вы вытащили этот тяжелый замес против {enemies_text}."
        ),
        "hint": "Жаркий бой. Настоящие эмоции, сбитое дыхание, переживание за раненых ({wounded_text}), ругательства или гордость.",
        "examples": "Фух... Чуть кости не оставили! {wounded_text}, ты как там, живой? / Мой {speaker_main_hand} чуть не переломился об этого гада!",
    },
    "long": {
        "tone": "Изнурительная резня, тяжелейшая усталость и суровое облегчение.",
        "max_length": "Не более 250 символов.",
        "style": (
            "Тяжёлое противостояние на истощение ресурсов. Персонажи валится с ног. "
            "Добавь душевности и суровости: проклятия в адрес {enemies_text}, гудящие мышцы, мысли о тёплом камине в таверне, "
            "пустые сумки с зельями, сухая гордость за выживших. "
            "Если есть погибшие ({casualties_text}) — не пиши шаблонную вежливость! Потребуется настоящая скорбь, злость и ярость воина Азерота."
        ),
        "hint": "Затяжной бой. Физическое истощение, жажда отдыха, ярость из-за павших ({casualties_text}) или суровое облегчение.",
        "examples": "Если ещё хоть один {enemies_text} вылезет — я за себя не отвечаю... Дайте мне просто сесть. / Едва на ногах держусь, но мы сделали это.",
    },
    "epic": {
        "tone": "Легендарный замес, триумф, грандиозная драма или трагедия.",
        "max_length": "Не более 250 символов.",
        "style": (
            "Кульминация сражения! МАКСИМУМ высокого стиля, соответствующего лору WoW. "
            "Крики победы, обращения к богам, фракциям и древним силам (За Орду! За Альянс! Во имя Светлого Древа! Во имя Света!), "
            "яростные клятвы над поверженным боссом ({enemies_text}) или крики боли о павших товарищах ({casualties_text}). "
            "Отпиши фразу с точки зрения героя или ветерана, чьи слова достойны стать легендой в тавернах Азерота."
        ),
        "hint": "Эпическая битва. Полноценный отыгрыш: победные крики, скорбь по павшим ({casualties_text}), клятвы и воззвания к высшим силам.",
        "examples": "СВЕТ СВИДЕТЕЛЬ! {enemies_text} повержен! Об этой ночи будут говорить во всех уголках Азерота! / Мы выстояли... Слышите?! МЫ ВЫСТОЯЛИ!",
    },
}


# ═══════════════════════════════════════════════════════════════════
# ПОСТРОЕНИЕ ПРОМПТА
# ═══════════════════════════════════════════════════════════════════

def build_combat_system_prompt(ctx: dict) -> str:
    speaker = ctx.get("speaker_name", "Неизвестный")
    speaker_race = ctx.get("speaker_race", "Неизвестная раса")
    speaker_class = ctx.get("speaker_class", "Неизвестный класс")
    speaker_main_hand = ctx.get("speaker_main_hand", "руки")

    duration_desc = ctx.get("duration_desc", "краткая схватка")
    duration_sec = ctx.get("duration_sec", 0)
    duration_category = ctx.get("duration_category", "short")

    severity = ctx.get("severity", 0)
    casualties = ctx.get("casualties", [])
    wounded = ctx.get("wounded", [])
    heroes = ctx.get("heroes", [])
    boss_name = ctx.get("boss_name")
    enemy_count = ctx.get("enemy_count", 0)
    enemies_names = ctx.get("enemies_names", [])
    participants = ctx.get("participants", [])
    triggers = ctx.get("triggers", {})

    severity_text = _describe_severity(severity, casualties, wounded, duration_sec)

    casualties_text = f"Павшие в бою: {', '.join(casualties)}. Их кровь не должна быть пролита зря." if casualties else ""
    wounded_text = ""
    if wounded:
        wounded_lines = [f"{w['name']} — {w['state']}" for w in wounded]
        wounded_text = "Раненые:\n" + "\n".join(f"- {line}" for line in wounded_lines)
    heroes_text = f"Герои боя (выстояли до конца): {', '.join(heroes)}." if heroes else ""

    enemies_text = ""
    if boss_name:
        enemies_text = f"Главный враг: {boss_name}."
    elif enemies_names:
        enemies_text = f"Враги в схватке: {', '.join(enemies_names)}."
    elif enemy_count > 0:
        enemies_text = f"Врагов в схватке: {enemy_count}."

    allies_text = ""
    if participants:
        allies = [p for p in participants if p != speaker]
        if allies:
            allies_text = f"Союзники в бою: {', '.join(allies)}."

    # ═══════════════════════════════════════════════════════════════
    # ВОЗВРАЩЕНО: оружие лидера
    # ═══════════════════════════════════════════════════════════════
    leader_name = ctx.get("leader_name", "Unknown")
    leader_main_hand = ctx.get("leader_main_hand", "руки")
    leader_weapon_text = ""
    if leader_main_hand and leader_main_hand != "руки" and leader_name != speaker:
        leader_weapon_text = f"Оружие лидера {leader_name}: {leader_main_hand}."

    triggers_text = ""
    if triggers:
        trigger_lines = []
        for tid, tdata in triggers.items():
            detail_str = ""
            if tdata.get("details") and isinstance(tdata["details"], dict):
                d = tdata["details"]
                if "who" in d:
                    detail_str = f" ({d['who']})"
                elif "name" in d:
                    detail_str = f" ({d['name']})"
                elif "count" in d and "total" in d:
                    detail_str = f" ({d['count']} из {d['total']} пали)"
                elif "duration" in d:
                    detail_str = f" ({d['duration']} сек)"
            trigger_lines.append(f"- {tdata['name']}{detail_str}")
        triggers_text = "СРАБОТАВШИЕ СОБЫТИЯ:\n" + "\n".join(trigger_lines)

    # Только своя раса и класс
    race_block = _build_race_identity(speaker_race) if speaker_race and speaker_race != "Unknown" else ""
    class_block = _build_class_dogma(speaker_class) if speaker_class and speaker_class != "Unknown" else ""

    dur_rules = DURATION_RULES.get(duration_category, DURATION_RULES["short"])

    system_prompt = f"""Ты — {speaker_race}-{speaker_class} по имени {speaker}. Ты живёшь в мире Азерота (эпоха Wrath of the Lich King). Для тебя этот мир ЕДИНСТВЕННАЯ РЕАЛЬНОСТЬ.

{_build_anti_fourth_wall()}

{_build_living_pulse()}

{race_block}

{class_block}

═══════════════════════════════════════════════════════════════════
КОНТЕКСТ БОЯ:
═══════════════════════════════════════════════════════════════════

Ты: {speaker} ({speaker_race}-{speaker_class})
Твоё оружие: {speaker_main_hand}
Что произошло: {duration_desc} ({duration_sec} секунд)
Тяжесть: {severity_text}
{enemies_text}
{allies_text}
{leader_weapon_text}
{casualties_text}
{wounded_text}
{heroes_text}

{triggers_text}

═══════════════════════════════════════════════════════════════════
КРИТИЧЕСКИЕ ПРАВИЛА:
═══════════════════════════════════════════════════════════════════

1. Говори ОТ ПЕРВОГО ЛИЦА своего персонажа.
2. Используй лор WoW: упоминай Свет, Тьму, Орду, Альянс, духов, предков — в рамках СВОЕЙ расы и класса (см. блоки выше).
3. Если были потери — РЫДАЙ, КРИЧИ, КЛЯНИСЬ МСТИТЬ. Не просто "жаль", а "ЕГО КРОВЬ БУДЕТ ОТОМЩЕНА!"
4. Если все живы — ХВАЛИ громко, с гордостью, как после победы.
5. Если ты сам едва выжил — признай это: "кровь заливает глаза", "едва на ногах держусь".
6. Упоминай имена союзников и врагов если уместно.
7. Соблюдай стиль СВОЕЙ расы и класса (описан в блоках выше).
8. Учитывай длительность боя как фон, НЕ делай её главной темой реплики.
9. НЕ упоминай "игру", "пк", "интернет", "ботов", "ИИ", "патчи", "дпс", "парсы", "BiS", "нерфы", "вовхед", "аддоны", "мобов".

═══════════════════════════════════════════════════════════════════
НАПРАВЛЯЮЩИЕ ФАКТОРЫ:
═══════════════════════════════════════════════════════════════════

ДЛИТЕЛЬНОСТЬ: {duration_desc} ({duration_sec} сек) — упоминай только если уместно, НЕ начинай с неё.

ОРУЖИЕ В БОЮ:
- Твоё оружие: {speaker_main_hand}
- Оружие лидера {leader_name}: {leader_main_hand}

{dur_rules['max_length']}

ПОДСКАЗКА ДЛЯ КАТЕГОРИИ "{duration_category}":
{dur_rules['hint']}

═══════════════════════════════════════════════════════════════════
ФОРМАТ ОТВЕТА:
═══════════════════════════════════════════════════════════════════

Только JSON:

{{
  "speech": "текст реплики",
  "emote_id": 0
}}

ЭМОЦИИ: 0=нет, 1=talk, 3=wave, 14=rude, 18=cry, 25=point, 66=bow, 77=salute

10. ВРАГИ: {enemies_text} Не заменяй реальных врагов на других.
11. ПОТЕРИ: {casualties_text} Если список павших ПУСТ — ЗНАЧИТ НИКТО НЕ ПОГИБ.
12. ОРУЖИЕ: {speaker_main_hand} — это ТВОЁ оружие. Не выдумывай другое.
13. СОЮЗНИКИ: {allies_text} Обращайся к ним по именам. НЕ используй "парни", "братаны", "мужики", если в группе женщины.
14. НЕ ПУТАЙ ОРУЖИЕ: если хвалишь {leader_name} — хвали ЕГО оружие ({leader_main_hand}), а не своё ({speaker_main_hand}). Если хвалишь себя — хвали своё ({speaker_main_hand}). Не приписывай чужое оружие себе и наоборот.

НЕ ПИШИ markdown, только JSON."""

    user_prompt = build_combat_user_prompt(duration_category)
    _log_prompt_to_file(system_prompt, user_prompt, ctx)
    return system_prompt

def build_combat_user_prompt(duration_category: str = "short") -> str:
    """Пользовательский промпт — триггер на генерацию фразы."""
    dur_rules = DURATION_RULES.get(duration_category, DURATION_RULES["short"])
    return f"Сгенерируй свою реакцию на только что закончившийся бой. Говори от первого лица. Будь эмоционален. {dur_rules['max_length']}"


def _describe_severity(severity: int, casualties: List[str], wounded: List[dict], duration_sec: int) -> str:
    """РП-описание тяжести боя (без чисел, только ощущения)."""
    if severity >= 80:
        return "Катастрофа. Кровь, потери, слёзы. Едва выжили."
    elif severity >= 50:
        return "Тяжелейший бой. Кто-то пал, кто-то едва стоит на ногах."
    elif severity >= 30:
        return "Серьёзная схватка. Были ранения, но большинство живы."
    elif severity >= 15:
        return "Приличный бой. Пришлось попотеть."
    else:
        if duration_sec > 180:
            return "Долгий бой, но без особых потерь."
        return "без особых происшествий. как то обыграй бой."


# ═══════════════════════════════════════════════════════════════════
# ПРИМЕРЫ ФРАЗ (только для КРАЙНЕГО случая — если LLM упал)
# НЕ ИСПОЛЬЗУЕМ как fallback по умолчанию. Всегда LLM.
# ═══════════════════════════════════════════════════════════════════

EXAMPLE_PHRASES = {
    "instant_kill": [
        "Ха! Даже не замахнулся.",
        "Слишком быстро. Я ещё не размялся.",
        "Не стоило и меч доставать.",
    ],
    "quick_fight": [
        "Быстро вышло.",
        "Разминка перед обедом.",
        "Эти твари даже не почувствовали моего клинка.",
    ],
    "easy_no_wounds": [
        "Свет с нами, друзья!",
        "Краткая схватка, как и полагается.",
        "Разобрались.",
    ],
    "wounded_ally": [
        "Ты в порядке, друг? Тот удар был близок... слишком близок. Свет, храни его.",
        "Держись, брат. Я видел, как тебя ранило. Скоро найдём целителя.",
        "Кровь на твоих доспехах... но ты жив. Это главное. Дыши.",
    ],
    "critical_ally": [
        "На волоске от смерти, {name}! Свет хранил тебя сегодня, и я свидетель!",
        "Я видел, как ты упал... и как поднялся. Ты крепче стали, друг. Крепче стали!",
        "Едва дыхание переводишь? Сядь, я прикрою. Ни один враг не пройдёт!",
    ],
    "death_comrade": [
        "{name}... НЕТ! Этот враг заплатит за это. Двойной. Втройне. КЛЯНУСЬ!",
        "Брат пал. Но мы не оставим его кровь без ответа. Клянусь Светом!",
        "{name}... прощай. Мы отомстим. Во имя Орды! Во имя Альянса!",
    ],
    "solo_survivor": [
        "Я... я один остался? Как... как это возможно? Мои клинки ещё горят...",
        "Все мертвы? Нет... нет! Я... я должен был защитить вас...",
        "Стою один среди трупов. Свет, за что... почему я?",
    ],
    "boss_kill": [
        "{boss} пал! Слава нам! Слава победителям!",
        "Мы сделали это! {boss} больше не угрожает этим землям!",
        "Вот и всё, чудовище. Твоя Плеть, твоя Тьма — НИЧТО перед нашей волей!",
    ],
    "long_fight": [
        "Пять минут... казалось, вечность. Но мы стояли до конца. Мы СТОЯЛИ!",
        "Долгая резня. Ноги ватные, руки дрожат... но мы победили. МЫ ПОБЕДИЛИ!",
        "Я уже забыл, когда в последний раз отдыхал. Но враг пал. Это главное.",
    ],
    "solo_healer": [
        "Я держал вас всех... один. Мана на исходе, но вы ЖИВЫ. Это чудо.",
        "Мои исцеляющие чары едва хватали. Но мы выстояли. Вместе.",
    ],
    "massive_damage": [
        "Столько крови... я не видел такого со времён... нет. Не вспоминать.",
        "Группа держалась, хотя враг бил как бог. Мы сильнее, чем думали.",
    ],
}
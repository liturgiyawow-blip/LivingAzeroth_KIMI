"""
tactical_planner.py — Генератор тактических планов для ботов WoW

Этот модуль — "мозг дирижёра". Он получает тактическую команду от игрока,
отправляет её в LLM (LM Studio) с детальным системным промптом,
получает структурированный JSON-план и сохраняет его в БД.

Архитектура:
  Вход:  событие "tactic_command_received" от EventBus
         (содержит: текст команды, имя игрока, GUID ботов, контекст)
  Выход: записи в ai_tactic_plans + ai_tactics
         (план разбит по шагам для каждого бота)

Пример работы:
  Игрок: "@all агрим босса, адды на танке, если у меня хп < 50% — хилите меня"
  LLM генерирует план с фазами: pull → main → emergency
  Каждая фаза содержит шаги для конкретных ролей (tank/heal/dps)
"""

import json
import uuid
import time
import logging
import threading
from typing import Dict, List, Optional, Any
from concurrent.futures import Future

# Импорты из нашего проекта
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.llm_queue import PriorityLLMQueue
from core.event_bus import EventBus
from core.world_state import WorldState
from wow_connector.db_bridge import WoWDBBridge

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# СИСТЕМНЫЙ ПРОМПТ ДЛЯ LLM-ДИРИЖЁРА
# ═══════════════════════════════════════════════════════════════════

_TACTICAL_PLANNER_PROMPT = """Ты — тактический дирижёр ботов в World of Warcraft (Wrath of the Lich King 3.3.5a).

ТВОЯ ЗАДАЧА: Преврати естественную команду игрока в строгий JSON-план действий для ботов.

═══════════════════════════════════════════════════════════════════
ДОСТУПНЫЕ КОМАНДЫ БОТАМ (playerbots mod):
═══════════════════════════════════════════════════════════════════

Боевые стратегии (co = combat):
  co +tank          — танк агрит, держит угрозу
  co +tank assist   — автопереагривание на хилов/дд
  co +tank face     — разворачивать босса спиной к рейду
  co +pull          — пуллить издалека
  co +pull back     — пуллить и отбегать
  co +dps           — максимальный урон одной цели
  co +dps assist    — атаковать цель лидера
  co +heal          — фокус на исцелении
  co +healer dps    — хил может дамажить если мана полная
  co +save mana     — экономить ману
  co +assist        — бить цель лидера
  co +focus         — не спамить дебаффы, бить одну цель
  co +aoe           — заливать паки АоЕ
  co +cc            — контроль толпы (овца, трапа, страх)
  co +threat        — контроль агро (стопать урон при 90%)
  co +avoid aoe     — выбегать из луж
  co +behind        — мили за спину цели
  co +wait for attack time [X] — ждать X сек перед атакой

Небоевые стратегии (nc = non-combat):
  nc +follow        — следовать за лидером
  nc +stay          — стоять на месте
  nc +loot          — собирать лут
  nc +food          — есть/пить вне боя

Быстрые команды (чат):
  attack            — атаковать цель лидера
  follow            — бежать к лидеру
  stay              — стоять на месте
  flee              — бежать к лидеру, игнорируя всё
  tank attack       — танк в атаку
  max dps           — дд в режим макс урона
  cast [spell]      — каст конкретного спелла
  cast [spell] on [name] — каст на цель

RTI-метки (рейдовые иконки):
  skull, cross, circle, star, square, triangle, diamond, moon

═══════════════════════════════════════════════════════════════════
СТРУКТУРА ОТВЕТА (строгий JSON):
═══════════════════════════════════════════════════════════════════

{
  "plan_name": "Краткое название плана",
  "global_strategy": "co +tank,-dps assist,+heal,nc +follow",  // стратегия для всех
  "phases": [
    {
      "phase_id": 1,
      "phase_name": "название_фазы_латиницей_без_пробелов",
      "trigger": "manual_start",
      "steps": [
        {
          "step_id": "s1",
          "actor_filter": "@tank",      // @all, @tank, @heal, @dps, @ranged, @melee, @маг, @Alfonso
          "action": "pull",             // attack, heal, stay, follow, cast, strategy, pull, flee, wait
          "target": "boss",             // boss, skull, cross, leader, self, spell_name
          "target_rti": "skull",        // или null
          "strategy_cmd": "co +tank,-threat", // или null
          "condition": null,            // условие выполнения (см. ниже)
          "timeout_sec": 10
        }
      ]
    },
    {
      "phase_id": 2,
      "phase_name": "main_combat",
      "trigger": "tank_has_aggro",
      "steps": [...]
    },
    {
      "phase_id": 3,
      "phase_name": "emergency_heal",
      "trigger": "health_below",
      "trigger_param": {"target": "leader", "value": 50},
      "priority": "emergency",
      "steps": [
        {
          "step_id": "s5",
          "actor_filter": "@heal",
          "action": "heal",
          "target": "leader",
          "condition": {
            "type": "health_below",
            "target": "leader",
            "value": 50,
            "unit": "percent"
          },
          "strategy_cmd": "co +heal,-dps",
          "interrupt_previous": true,
          "timeout_sec": 15
        }
      ]
    }
  ],
  "fallback": {
    "on_wipe": "flee",
    "on_leader_death": "revive_and_wait"
  }
}

═══════════════════════════════════════════════════════════════════
УСЛОВИЯ (condition):
═══════════════════════════════════════════════════════════════════

Типы условий:
  - null                    — нет условия, выполнять сразу
  - health_below            — хп ниже X%
  - health_above            — хп выше X%
  - mana_below              — мана ниже X%
  - threat_above            — агро выше X% (для дд)
  - buff_missing            — нет баффа (по имени)
  - debuff_present          — есть дебафф (по имени)
  - enemy_count_above       — врагов больше X
  - phase_time_elapsed      — прошло X сек с начала фазы

Структура:
  {
    "type": "health_below",
    "target": "leader",       // leader, tank, self, @heal, @all
    "value": 50,
    "unit": "percent"         // percent, absolute, seconds
  }

═══════════════════════════════════════════════════════════════════
ПРАВИЛА:
═══════════════════════════════════════════════════════════════════

1. Разбивай бой на ЛОГИЧЕСКИЕ ФАЗЫ (pull → main → emergency → finish)
2. Каждая фаза имеет ТРИГГЕР — что её запускает
3. Emergency-фазы имеют priority: "emergency" и прерывают предыдущие
4. Для сложных команд создавай несколько фаз с условиями
5. actor_filter используй точно: @tank, @heal, @dps, @ranged, @melee, или @имя
6. strategy_cmd — только если нужно ПЕРЕКЛЮЧИТЬ стратегию бота
7. НЕ ПИШИ markdown, НЕ ПИШИ комментарии в JSON
8. Отвечай ТОЛЬКО валидным JSON, без пояснений

═══════════════════════════════════════════════════════════════════
ПРИМЕРЫ КОМАНД И ПЛАНЫ:
═══════════════════════════════════════════════════════════════════

Команда: "агрим босса, адды на танке"
План: фаза1(pull): танк pull на skull → фаза2(main): все attack skull, танк co+tank

Команда: "если у меня хп меньше половины, хилите меня"
План: фаза1(main): обычный бой → фаза2(emergency): @heal heal leader при health_below 50%

Команда: "дд бейте аддов, танк держи босса"
План: фаза1(pull): танк attack boss → фаза2(main): @dps attack cross (адды), танк stay на боссе

Команда: "всем стоять, я пойду агрить"
План: фаза1(setup): @all stay → фаза2(pull): лидер агрит → фаза3(main): @all attack"""

# ═══════════════════════════════════════════════════════════════════
# КЛАСС: TacticalPlanner
# ═══════════════════════════════════════════════════════════════════

class TacticalPlanner:
    """
    Генератор тактических планов для ботов.

    Как дирижёр оркестра: получает общую идею от игрока,
    разбивает её на партии (роли), назначает каждой партию
    ноты (команды) и собирает всё в единый план-симфонию.

    Аргументы конструктора:
        llm_queue: PriorityLLMQueue — очередь к LM Studio
        event_bus: EventBus — шина событий (подписываемся на tactic_command_received)
        db_bridge: WoWDBBridge — мост к MySQL для записи планов
        world_state: WorldState — состояние мира (для контекста)

    Пример использования:
        planner = TacticalPlanner(llm_queue, event_bus, db_bridge, world_state)
        # Автоматически подписывается на события и начинает работать
    """

    def __init__(self, llm_queue: PriorityLLMQueue, event_bus: EventBus,
                 db_bridge: WoWDBBridge, world_state: WorldState):
        self.llm = llm_queue
        self.bus = event_bus
        self.db = db_bridge
        self.world = world_state

        # Подписаться на событие получения тактической команды
        self.bus.subscribe("tactic_command_received", self._on_tactic_command)

        # Кэш активных планов (plan_id -> данные) для быстрого доступа
        # Используем dict с ограничением по размеру (анти-утечка памяти)
        self._active_plans: Dict[str, dict] = {}
        self._plans_lock = threading.Lock()

        logger.info("TacticalPlanner initialized and subscribed to events")

    # ═══════════════════════════════════════════════════════════════
    # ОБРАБОТЧИК СОБЫТИЯ: получена тактическая команда
    # ═══════════════════════════════════════════════════════════════

    def _on_tactic_command(self, payload: dict):
        """
        Вызывается когда EventBus публикует событие tactic_command_received.
        Это стартовая точка — здесь начинается генерация плана.

        Аргументы:
            payload: dict с ключами:
                - player_guid: int
                - player_name: str
                - bot_guid: int (GUID одного бота, но команда для всей группы)
                - bot_name: str
                - command: str (текст команды игрока)
                - classification: dict (результат классификатора)
                - timestamp: float
        """
        player_guid = payload.get("player_guid", 0)
        player_name = payload.get("player_name", "Player")
        command = payload.get("command", "")
        bot_guid = payload.get("bot_guid", 0)

        logger.info("=" * 50)
        logger.info("TACTIC PLANNER: New command from %s: '%s'", player_name, command)

        # Получить контекст группы (все боты в пати игрока)
        group_context = self._get_group_context(player_guid)

        if not group_context or not group_context.get("bots"):
            logger.warning("No bots found in group for player %s", player_name)
            # Всё равно генерируем план, но только для одного бота
            group_context = {
                "bots": [{"guid": bot_guid, "name": payload.get("bot_name", "Bot"),
                         "role": "dps", "class": "Warrior"}],
                "size": 1,
                "instance_type": "world"
            }

        # Сформировать промпт для LLM
        user_prompt = self._build_planner_prompt(command, player_name, group_context)

        # Отправить в LLM с высоким приоритетом (планирование — важная задача)
        future = self.llm.submit(
            system_prompt=_TACTICAL_PLANNER_PROMPT,
            user_prompt=user_prompt,
            temperature=0.2,      # Низкая температура = точный, предсказуемый план
            max_tokens=800,       # План может быть большим
            priority=1            # Высший приоритет
        )

        # Обработать результат в отдельном потоке (не блокировать EventBus)
        threading.Thread(
            target=self._process_plan,
            args=(future, player_guid, player_name, group_context, command),
            daemon=True,
            name=f"PlanProcessor-{player_guid}"
        ).start()

    # ═══════════════════════════════════════════════════════════════
    # ФОРМИРОВАНИЕ ПРОМПТА ДЛЯ LLM
    # ═══════════════════════════════════════════════════════════════

    def _build_planner_prompt(self, command: str, player_name: str,
                              group_context: dict) -> str:
        """
        Собрать промпт для LLM из команды игрока и контекста группы.

        Это как "заполнить брифинг" для дирижёра — даём ему всю
        необходимую информацию о составе оркестра и желаемой пьесе.

        Аргументы:
            command: текст команды игрока
            player_name: имя лидера
            group_context: данные о группе и ботах

        Возвращает:
            str: готовый промпт для LLM
        """
        bots_info = []
        for bot in group_context.get("bots", []):
            bots_info.append(
                f"- {bot['name']} ({bot['class']}, роль: {bot['role']}, GUID: {bot['guid']})"
            )

        bots_text = "\n".join(bots_info) if bots_info else "- Нет данных о ботах"

        return f"""ИГРОК: {player_name}
КОМАНДА: "{command}"

СОСТАВ ГРУППЫ ({group_context.get('size', 1)} ботов):
{bots_text}

ТИП МЕСТНОСТИ: {group_context.get('instance_type', 'world')}

Сгенерируй тактический план в формате JSON."""

    # ═══════════════════════════════════════════════════════════════
    # ПОЛУЧЕНИЕ КОНТЕКСТА ГРУППЫ ИЗ БД
    # ═══════════════════════════════════════════════════════════════

    def _get_group_context(self, player_guid: int) -> Optional[dict]:
        """
        Получить информацию о группе игрока из базы данных.

        Ищем в таблице group_member всех членов группы,
        затем в characters получаем имена, классы, уровни.

        Аргументы:
            player_guid: GUID лидера группы

        Возвращает:
            dict с bots, size, instance_type или None
        """
        try:
            conn = self.db._get_conn()
            with conn.cursor() as cur:
                # Найти группу, в которой состоит игрок
                cur.execute(
                    "SELECT guid FROM group_member WHERE memberGuid = %s",
                    (player_guid,)
                )
                row = cur.fetchone()
                if not row:
                    return None

                group_guid = row[0]

                # Получить всех членов группы
                cur.execute(
                    """SELECT gm.memberGuid, c.name, c.class, c.level, c.online
                       FROM group_member gm
                       JOIN characters c ON gm.memberGuid = c.guid
                       WHERE gm.guid = %s""",
                    (group_guid,)
                )
                members = cur.fetchall()

                bots = []
                for member in members:
                    m_guid, m_name, m_class, m_level, m_online = member
                    # Пропускаем самого игрока (он не бот)
                    if m_guid == player_guid:
                        continue

                    # Определяем роль по классу
                    role = self._class_to_role(m_class)

                    bots.append({
                        "guid": m_guid,
                        "name": m_name,
                        "class": self._class_id_to_name(m_class),
                        "level": m_level,
                        "role": role,
                        "online": bool(m_online)
                    })

                return {
                    "bots": bots,
                    "size": len(bots) + 1,  # +1 сам игрок
                    "instance_type": "dungeon"  # TODO: определять по карте
                }

        except Exception as e:
            logger.error("Failed to get group context: %s", e)
            return None
        finally:
            if 'conn' in locals():
                conn.close()

    # ═══════════════════════════════════════════════════════════════
    # ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _class_to_role(class_id: int) -> str:
        """
        Преобразовать ID класса WoW в боевую роль.

        Классы WoW (3.3.5a):
          1=Warrior → tank/dps
          2=Paladin → tank/heal/dps
          3=Hunter  → dps (ranged)
          4=Rogue   → dps (melee)
          5=Priest  → heal/dps
          6=DeathKnight → tank/dps
          7=Shaman  → heal/dps
          8=Mage    → dps (ranged)
          9=Warlock → dps (ranged)
          11=Druid  → tank/heal/dps

        Для простоты используем дефолтную роль по классу.
        """
        role_map = {
            1: "tank",    # Warrior (может быть дд, но по умолчанию танк)
            2: "heal",    # Paladin (может быть танком, но по умолчанию хил)
            3: "dps",     # Hunter
            4: "dps",     # Rogue
            5: "heal",    # Priest
            6: "tank",    # DeathKnight
            7: "heal",    # Shaman
            8: "dps",     # Mage
            9: "dps",     # Warlock
            11: "dps",    # Druid (может быть всем, по умолчанию дд)
        }
        return role_map.get(class_id, "dps")

    @staticmethod
    def _class_id_to_name(class_id: int) -> str:
        """Преобразовать ID класса в название."""
        names = {
            1: "Warrior", 2: "Paladin", 3: "Hunter", 4: "Rogue",
            5: "Priest", 6: "DeathKnight", 7: "Shaman", 8: "Mage",
            9: "Warlock", 11: "Druid"
        }
        return names.get(class_id, "Unknown")

    # ═══════════════════════════════════════════════════════════════
    # ОБРАБОТКА ОТВЕТА ОТ LLM
    # ═══════════════════════════════════════════════════════════════

    def _process_plan(self, future: Future, player_guid: int,
                      player_name: str, group_context: dict,
                      original_command: str):
        """
        Обработать ответ LLM, распарсить JSON-план и сохранить в БД.

        Этот метод выполняется в отдельном потоке, поэтому
        может делать долгие операции (запись в БД) без блокировки.

        Аргументы:
            future: Future от LLM (результат придёт асинхронно)
            player_guid, player_name: данные игрока
            group_context: контекст группы
            original_command: оригинальная команда (для логов)
        """
        try:
            # Ждём результат от LLM (макс 15 сек)
            result = future.result(timeout=15.0)
            logger.info("LLM plan received for %s", player_name)

            # Парсим JSON-план из ответа
            plan_data = self._parse_plan_json(result, original_command)

            if not plan_data:
                logger.error("Failed to parse plan for '%s'", original_command)
                return

            # Сохранить план в БД
            plan_id = self._save_plan_to_db(
                plan_data, player_guid, player_name,
                group_context, original_command
            )

            if plan_id:
                logger.info("Plan %s saved successfully (%d phases)",
                           plan_id, len(plan_data.get("phases", [])))

                # Оповестить систему о новом плане
                self.bus.publish("tactic_plan_created", {
                    "plan_id": plan_id,
                    "player_guid": player_guid,
                    "player_name": player_name,
                    "phases_count": len(plan_data.get("phases", [])),
                    "command": original_command,
                })

        except Exception as e:
            logger.error("Plan processing failed: %s", e, exc_info=True)

    # ═══════════════════════════════════════════════════════════════
    # ПАРСИНГ JSON-ПЛАНА ИЗ ОТВЕТА LLM
    # ═══════════════════════════════════════════════════════════════

    def _parse_plan_json(self, raw: dict, original_command: str) -> Optional[dict]:
        if raw.get("error") or raw.get("fallback"):
            logger.warning("LLM error for plan, using fallback")
            return self._create_fallback_plan(original_command)

        # FIX: _safe_parse_json уже распарсил JSON в dict
        if isinstance(raw, dict) and "phases" in raw:
            logger.info("Plan already parsed by LLM queue, using directly")
            if isinstance(raw.get("phases"), list) and len(raw["phases"]) > 0:
                for phase in raw["phases"]:
                    if "steps" not in phase or not isinstance(phase["steps"], list):
                        logger.warning("Invalid phase structure")
                        return self._create_fallback_plan(original_command)
                return raw
            else:
                logger.warning("Direct parse has no valid phases")
                return self._create_fallback_plan(original_command)

        # Fallback: старая логика
        content = raw.get("speech", "") or str(raw)
        # ... остальной код без изменений ...
        # Извлекаем текст из ответа
        content = raw.get("speech", "") or str(raw)

        # Ищем JSON в тексте
        plan_json = self._extract_json(content)
        if not plan_json:
            logger.warning("No JSON found in LLM response, using fallback")
            return self._create_fallback_plan(original_command)

        try:
            plan = json.loads(plan_json)

            # Валидация структуры
            if "phases" not in plan or not isinstance(plan["phases"], list):
                logger.warning("Invalid plan structure: no 'phases' array")
                return self._create_fallback_plan(original_command)

            # Проверяем каждую фазу
            for phase in plan["phases"]:
                if "steps" not in phase or not isinstance(phase["steps"], list):
                    logger.warning("Invalid phase: no 'steps' array")
                    return self._create_fallback_plan(original_command)

            return plan

        except json.JSONDecodeError as e:
            logger.warning("JSON decode error: %s", e)
            return self._create_fallback_plan(original_command)

    @staticmethod
    def _extract_json(text: str) -> Optional[str]:
        """
        Извлечь JSON из текста, обрамлённого markdown или с пояснениями.

        Ищет первое вхождение {...} с вложенностью.
        """
        text = text.strip()

        # Убираем markdown-блоки
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        # Ищем JSON-объект
        start = text.find("{")
        if start == -1:
            return None

        # Считаем скобки для нахождения конца объекта
        brace_count = 0
        end = start
        for i, char in enumerate(text[start:], start):
            if char == "{":
                brace_count += 1
            elif char == "}":
                brace_count -= 1
                if brace_count == 0:
                    end = i + 1
                    break

        if brace_count != 0:
            return None  # Несбалансированные скобки

        return text[start:end]

    def _create_fallback_plan(self, command: str) -> dict:
        """
        Создать простейший fallback-план если LLM не справился.

        Это "план на всякий случай" — безопасный, не сломает бой.
        """
        logger.info("Creating fallback plan for: '%s'", command)

        return {
            "plan_name": "Fallback Plan",
            "global_strategy": "co +dps assist,nc +follow",
            "phases": [
                {
                    "phase_id": 1,
                    "phase_name": "default_action",
                    "trigger": "manual_start",
                    "steps": [
                        {
                            "step_id": "s1",
                            "actor_filter": "@all",
                            "action": "follow",
                            "target": "leader",
                            "condition": None,
                            "timeout_sec": 10
                        }
                    ]
                }
            ],
            "fallback": {
                "on_wipe": "flee",
                "on_leader_death": "revive_and_wait"
            }
        }

    # ═══════════════════════════════════════════════════════════════
    # СОХРАНЕНИЕ ПЛАНА В БАЗУ ДАННЫХ
    # ═══════════════════════════════════════════════════════════════

    def _save_plan_to_db(self, plan_data: dict, player_guid: int,
                         player_name: str, group_context: dict,
                         original_command: str) -> Optional[str]:
        """
        Сохранить план в ai_tactic_plans и шаги в ai_tactics.

        Раскладываем план по полочкам:
        - Мета-информация → ai_tactic_plans
        - Каждый шаг для каждого бота → ai_tactics

        Аргументы:
            plan_data: валидный JSON-план
            player_guid, player_name: данные игрока
            group_context: контекст группы
            original_command: оригинальная команда

        Возвращает:
            str: plan_id или None при ошибке
        """
        plan_id = str(uuid.uuid4())[:16]  # Короткий UUID
        timestamp = int(time.time())

        try:
            conn = self.db._get_conn()
            with conn.cursor() as cur:
                # 1. Сохранить мета-информацию о плане
                cur.execute("""
                    INSERT INTO ai_tactic_plans 
                    (plan_id, player_guid, player_name, group_size, 
                     instance_type, encounter_name, plan_json, status, started_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'active', %s)
                """, (
                    plan_id, player_guid, player_name,
                    group_context.get("size", 1),
                    group_context.get("instance_type", "world"),
                    plan_data.get("plan_name", "Unknown"),
                    json.dumps(plan_data, ensure_ascii=False),
                    timestamp
                ))

                # 2. Раскладываем шаги по ботам
                bots = group_context.get("bots", [])
                global_strategy = plan_data.get("global_strategy", "")

                for phase in plan_data.get("phases", []):
                    phase_id = phase.get("phase_id", 1)
                    phase_name = phase.get("phase_name", f"phase_{phase_id}")
                    trigger = phase.get("trigger", "manual_start")
                    trigger_param = phase.get("trigger_param")
                    priority = phase.get("priority", "normal")

                    for step in phase.get("steps", []):
                        step_id = step.get("step_id", "s0")
                        actor_filter = step.get("actor_filter", "@all")
                        action = step.get("action", "follow")
                        target = step.get("target", "leader")
                        target_rti = step.get("target_rti")
                        strategy_cmd = step.get("strategy_cmd")
                        if not strategy_cmd and global_strategy:
                            strategy_cmd = global_strategy
                        condition = step.get("condition")
                        timeout = step.get("timeout_sec", 10)
                        interrupt = step.get("interrupt_previous", False)

                        # Определяем каких ботов затрагивает фильтр
                        target_bots = self._resolve_actor_filter(
                            actor_filter, bots
                        )

                        for bot in target_bots:
                            # Сериализуем condition в JSON-строку
                            condition_str = json.dumps(condition, ensure_ascii=False) if condition else None
                            trigger_param_str = json.dumps(trigger_param, ensure_ascii=False) if trigger_param else None

                            cur.execute("""
                                INSERT INTO ai_tactics 
                                (plan_id, player_guid, player_name, bot_guid, bot_name, bot_role,
                                 phase_id, phase_name, step_id, step_order,
                                 action, target, target_rti, strategy_cmd,
                                 condition_json, priority, timeout_sec, created_at)
                                VALUES (%s, %s, %s, %s, %s, %s,
                                        %s, %s, %s, %s,
                                        %s, %s, %s, %s,
                                        %s, %s, %s, %s)
                            """, (
                                plan_id, player_guid, player_name,
                                bot["guid"], bot["name"], bot["role"],
                                phase_id, phase_name, step_id, step.get("step_order", 0),
                                action, target, target_rti, strategy_cmd,
                                condition_str, priority, timeout, timestamp
                            ))

                conn.commit()
                logger.info("Plan %s committed to DB (%d bots, %d phases)",
                           plan_id, len(bots),
                           len(plan_data.get("phases", [])))

                # Сохранить в кэш активных планов
                with self._plans_lock:
                    self._active_plans[plan_id] = {
                        "player_guid": player_guid,
                        "plan_data": plan_data,
                        "created_at": timestamp,
                        "status": "active"
                    }

                return plan_id

        except Exception as e:
            logger.error("Failed to save plan to DB: %s", e, exc_info=True)
            return None
        finally:
            if 'conn' in locals():
                conn.close()

    # ═══════════════════════════════════════════════════════════════
    # РАЗРЕШЕНИЕ ФИЛЬТРА АКТЁРОВ
    # ═══════════════════════════════════════════════════════════════

    def _resolve_actor_filter(self, actor_filter: str,
                              bots: List[dict]) -> List[dict]:
        """
        Преобразовать фильтр типа "@tank" или "@heal" в список конкретных ботов.

        Это как "распределить партии по музыкантам":
        @all → все боты
        @tank → только танки
        @Alfonso → только Alfonso

        Аргументы:
            actor_filter: строка фильтра (@all, @tank, @heal, @dps, @имя)
            bots: список всех ботов в группе

        Возвращает:
            List[dict]: отфильтрованные боты
        """
        filter_lower = actor_filter.lower().strip()

        # @all — все боты
        if filter_lower in ("@all", "@все", "@пати", "@группа"):
            return bots

        # @tank — танки
        if filter_lower in ("@tank", "@танк", "@танки"):
            return [b for b in bots if b["role"] == "tank"]

        # @heal — хилы
        if filter_lower in ("@heal", "@хил", "@хилы", "@лекарь"):
            return [b for b in bots if b["role"] == "heal"]

        # @dps — дамагеры
        if filter_lower in ("@dps", "@дд", "@дпс"):
            return [b for b in bots if b["role"] == "dps"]

        # @ranged — рдд
        if filter_lower in ("@ranged", "@рдд", "@дальний"):
            ranged_classes = {"Hunter", "Mage", "Warlock", "Priest", "Shaman", "Druid"}
            return [b for b in bots if b["class"] in ranged_classes]

        # @melee — мдд
        if filter_lower in ("@melee", "@мдд", "@ближний"):
            melee_classes = {"Warrior", "Paladin", "Rogue", "DeathKnight"}
            return [b for b in bots if b["class"] in melee_classes]

        # По имени бота: @Alfonso
        name_filter = filter_lower.lstrip("@")
        for bot in bots:
            if bot["name"].lower() == name_filter:
                return [bot]

        # Если ничего не подошло — возвращаем всех (безопасный fallback)
        logger.warning("Unknown actor filter '%s', using @all", actor_filter)
        return bots

    # ═══════════════════════════════════════════════════════════════
    # УПРАВЛЕНИЕ АКТИВНЫМИ ПЛАНАМИ
    # ═══════════════════════════════════════════════════════════════

    def get_active_plan(self, plan_id: str) -> Optional[dict]:
        """Получить активный план по ID."""
        with self._plans_lock:
            plan = self._active_plans.get(plan_id)
            return dict(plan) if plan else None

    def cancel_plan(self, plan_id: str) -> bool:
        """
        Отменить план (пометить в БД и удалить из кэша).

        Вызывается когда игрок даёт новую команду или при выходе из боя.
        """
        try:
            conn = self.db._get_conn()
            with conn.cursor() as cur:
                # Пометить план как отменённый
                cur.execute(
                    "UPDATE ai_tactic_plans SET status = 'cancelled' WHERE plan_id = %s",
                    (plan_id,)
                )
                # Отменить все невыполненные шаги
                cur.execute(
                    "UPDATE ai_tactics SET executed = 4 WHERE plan_id = %s AND executed = 0",
                    (plan_id,)
                )
                conn.commit()

            with self._plans_lock:
                if plan_id in self._active_plans:
                    self._active_plans[plan_id]["status"] = "cancelled"
                    del self._active_plans[plan_id]

            logger.info("Plan %s cancelled", plan_id)
            return True

        except Exception as e:
            logger.error("Failed to cancel plan %s: %s", plan_id, e)
            return False
        finally:
            if 'conn' in locals():
                conn.close()

    def complete_plan(self, plan_id: str) -> bool:
        """Завершить план (при успешном окончании боя)."""
        try:
            conn = self.db._get_conn()
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ai_tactic_plans SET status = 'completed', completed_at = %s WHERE plan_id = %s",
                    (int(time.time()), plan_id)
                )
                conn.commit()

            with self._plans_lock:
                if plan_id in self._active_plans:
                    del self._active_plans[plan_id]

            logger.info("Plan %s completed", plan_id)
            return True

        except Exception as e:
            logger.error("Failed to complete plan %s: %s", plan_id, e)
            return False
        finally:
            if 'conn' in locals():
                conn.close()


# ═══════════════════════════════════════════════════════════════════
# ФАБРИКА ДЛЯ ИНИЦИАЛИЗАЦИИ
# ═══════════════════════════════════════════════════════════════════

def create_tactical_planner(llm_queue: PriorityLLMQueue,
                            event_bus: EventBus,
                            db_bridge: WoWDBBridge,
                            world_state: WorldState) -> TacticalPlanner:
    """
    Фабричная функция для создания TacticalPlanner.

    Используется в main.py для регистрации модуля.

    Пример:
        from modules.tactical_ai.tactical_planner import create_tactical_planner
        planner = create_tactical_planner(llm_queue, event_bus, db_bridge, world_state)
    """
    return TacticalPlanner(llm_queue, event_bus, db_bridge, world_state)
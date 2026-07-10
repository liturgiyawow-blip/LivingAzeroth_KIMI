"""
MessageClassifier — классификатор сообщений игрока

Определяет: игрок хочет поболтать или дать тактическую команду?

Как работает:
1. Получает текст сообщения + контекст (кто говорит, куда, сколько ботов)
2. Отправляет в LLM (LM Studio) с системным промптом-классификатором
3. Получает JSON: {"type": "CHAT|TACTIC|MIXED", "confidence": 0.0-1.0}
4. Если уверенность низкая — по умолчанию считаем CHAT (безопаснее)

Важно: классификация — быстрая операция (1 токен ответа, ~50ms)
"""

import json
import logging
from typing import Dict, Optional

from concurrent.futures import Future

# Импортируем из родительского проекта
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.llm_queue import PriorityLLMQueue

logger = logging.getLogger(__name__)


class MessageClassifier:
    """
    Классификатор сообщений от игрока.

    Правило простое: если игрок говорит "идём", "атакуем", "хилите",
    "танчите", "бейте" — это ТАКТИКА.
    Если говорит "как дела", "лол", "спасибо" — это БОЛТОВНЯ.

    Но мы не парсим руками — отдаём LLM, потому что русский язык сложный:
    "пойдём босса мочить" = тактика, хотя "пойдём" похоже на приглашение.
    """

    def __init__(self, llm_queue: PriorityLLMQueue):
        """
        Конструктор классификатора.

        Аргументы:
            llm_queue: очередь к LLM (из core/llm_queue.py)
                       Классификатор использует приоритет 1 (самый высокий),
                       потому что ему нужен быстрый ответ.

        Пример:
            classifier = MessageClassifier(llm_queue)
        """
        self.llm = llm_queue
        logger.info("MessageClassifier initialized")

    # ========================================
    # СИСТЕМНЫЙ ПРОМПТ ДЛЯ КЛАССИФИКАЦИИ
    # ========================================
    _CLASSIFY_PROMPT = """Ты — классификатор сообщений в World of Warcraft.

ЗАДАЧА: Определи, хочет ли игрок ПОБОЛТАТЬ с ботами или дать ТАКТИЧЕСКУЮ КОМАНДУ.

ПРАВИЛА:
- TACTIC — если есть приказы, указания действий, координация боя
  Примеры: "идём на босса", "танки агрят", "хилы хилят", "дд бьют аддов",
           "всем стоять", "следуйте за мной", "атакуем цель"
- CHAT — если вопросы, приветствия, эмоции, сленг, болтовня
  Примеры: "как дела", "лол", "спасибо", "привет", "чё как",
           "классно тут", "кто хочет лута"
- MIXED — если и команда, и болтовня вместе
  Примеры: "го на босса, пацаны", "ладно, идём, но осторожно"

ОТВЕТ: Только JSON, без markdown, без объяснений:

{"type": "TACTIC|CHAT|MIXED", "confidence": 0.0-1.0, "reason": "кратко почему"}"""

    def classify(self, message: str, player_name: str = "Player",
                 channel: str = "SAY-BOT", bot_count: int = 1) -> Dict:
        """
        Классифицировать одно сообщение.

        Аргументы:
            message: текст сообщения игрока (без префикса @)
            player_name: имя игрока (для контекста)
            channel: канал (SAY-BOT, WHISPER, PARTY)
            bot_count: сколько ботов получили сообщение

        Возвращает:
            dict с ключами: type, confidence, reason

        Пример:
            result = classifier.classify("идём на босса", "Liturgiya")
            # result = {"type": "TACTIC", "confidence": 0.95, "reason": "приказ движения + цель"}
        """
        # Формируем промпт для LLM
        user_prompt = self._build_user_prompt(message, player_name, channel, bot_count)

        # Отправляем в LLM с высоким приоритетом (1 = быстро)
        future = self.llm.submit(
            system_prompt=self._CLASSIFY_PROMPT,
            user_prompt=user_prompt,
            temperature=0.1,      # Низкая температура = точный ответ
            max_tokens=50,        # Классификация — короткий ответ
            priority=1            # Высший приоритет
        )

        # Ждём результат (синхронно, т.к. классификация должна быть быстрой)
        try:
            result = future.result(timeout=5.0)
            return self._parse_result(result, message)
        except Exception as e:
            logger.error("Classification failed for '%s': %s", message, e)
            # Если LLM не ответил — считаем CHAT (безопасный fallback)
            return {"type": "CHAT", "confidence": 0.0, "reason": "LLM error, fallback to CHAT"}

    def _build_user_prompt(self, message: str, player_name: str,
                          channel: str, bot_count: int) -> str:
        """
        Собрать промпт для LLM из данных сообщения.

        Это как "заполнить бланк" — берём шаблон и вставляем данные.
        """
        return f"""Игрок: {player_name}
Канал: {channel}
Ботов получило: {bot_count}
Сообщение: "{message}"

Классифицируй."""

    def _parse_result(self, raw: dict, original_msg: str) -> dict:
        """
        Разобрать ответ LLM в структурированный dict.

        Защита от "дурака": если LLM вернул что-то не то —
        подставляем безопасный CHAT.

        Аргументы:
            raw: сырой ответ от LLM (уже распарсенный JSON или fallback)
            original_msg: оригинальное сообщение (для логов)

        Возвращает:
            Нормализованный dict с type, confidence, reason
        """
        # Если LLM вернул ошибку — fallback
        if raw.get("error") or raw.get("fallback"):
            logger.warning("LLM fallback for '%s'", original_msg)
            return {"type": "CHAT", "confidence": 0.0, "reason": "LLM error"}

        # Пытаемся извлечь JSON из ответа
        content = raw.get("speech", "") or str(raw)
        
        try:
            # Если LLM вернул JSON внутри текста — парсим
            if isinstance(content, str):
                # Ищем JSON в тексте
                start = content.find("{")
                end = content.rfind("}")
                if start != -1 and end != -1:
                    data = json.loads(content[start:end+1])
                else:
                    data = json.loads(content)
            else:
                data = content

            # Проверяем обязательные поля
            msg_type = data.get("type", "CHAT").upper()
            if msg_type not in ("TACTIC", "CHAT", "MIXED"):
                msg_type = "CHAT"

            confidence = float(data.get("confidence", 0.5))
            confidence = max(0.0, min(1.0, confidence))  # Обрезаем 0-1

            reason = data.get("reason", "no reason given")

            result = {
                "type": msg_type,
                "confidence": confidence,
                "reason": reason
            }

            logger.info("Classified '%s' → %s (%.2f): %s",
                       original_msg, msg_type, confidence, reason)
            return result

        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning("Failed to parse LLM response for '%s': %s", original_msg, e)
            return {"type": "CHAT", "confidence": 0.0, "reason": "parse error, fallback"}

    # ========================================
    # БЫСТРАЯ ПРОВЕРКА (без LLM)
    # ========================================
    _TACTIC_KEYWORDS = {
        # Русские
        "иди", "идём", "идем", "го", "пойдём", "пойдем",
        "атак", "бей", "бить", "убивай", "фокус", "дамаг",
        "танч", "агр", "держи", "держать",
        "хил", "лечи", "хилить", "баф", "бафай",
        "стой", "стоять", "стойте", "жди", "ждать",
        "следуй", "следуйте", "фоллов", "follow",
        "каст", "кастуй", "кастовать",
        "вперёд", "вперед", "назад", "влево", "вправо",
        "босс", "адд", "адды", "моб", "мобы", "треш",
        "пул", "пулить", "пулл",
        # Английские
        "attack", "go", "follow", "stay", "heal", "tank",
        "buff", "cast", "pull", "focus", "kill", "dmg",
    }

    def quick_check(self, message: str) -> Optional[Dict]:
        """
        Быстрая проверка без LLM — по ключевым словам.

        Если находим тактическое слово — сразу возвращаем TACTIC
        без ожидания LLM. Экономит время на очевидных командах.

        Аргументы:
            message: текст сообщения

        Возвращает:
            dict если нашли ключевое слово, None если нет

        Пример:
            result = classifier.quick_check("идём на босса")
            # result = {"type": "TACTIC", "confidence": 0.8, "reason": "keyword: идём"}
        """
        lower_msg = message.lower()

        for keyword in self._TACTIC_KEYWORDS:
            if keyword in lower_msg:
                logger.debug("Quick check TACTIC by keyword '%s' in '%s'", keyword, message)
                return {
                    "type": "TACTIC",
                    "confidence": 0.8,
                    "reason": f"keyword: {keyword}"
                }

        return None  # Не нашли — нужна классификация через LLM

    def classify_with_fallback(self, message: str, player_name: str = "Player",
                                channel: str = "SAY-BOT", bot_count: int = 1) -> Dict:
        """
        Умная классификация: сначала быстрая проверка, потом LLM.

        Это основной метод, который следует использовать.
        Работает быстро для очевидных команд, точно для сложных.

        Аргументы: те же, что и у classify()

        Возвращает: dict с классификацией

        Пример:
            result = classifier.classify_with_fallback("идём на босса")
            # Быстрая проверка сразу вернёт TACTIC, LLM не нужен

            result = classifier.classify_with_fallback("как дела?")
            # Быстрая проверка не сработает → пойдёт в LLM → CHAT
        """
        # Шаг 1: Пробуем быструю проверку
        quick = self.quick_check(message)
        if quick:
            return quick

        # Шаг 2: Если не уверены — спрашиваем LLM
        return self.classify(message, player_name, channel, bot_count)
"""
persona_loader.py — Загрузчик ролевых шаблонов для ботов

Позволяет менять личность всех ботов одним файлом.
Поддерживает горячую перезагрузку без рестарта сервера.
"""

import json
import logging
import threading
import time
from pathlib import Path
from typing import Dict, Optional, Any

import config

logger = logging.getLogger(__name__)


class PersonaLoader:
    """
    Загрузчик и менеджер ролевых шаблонов (personas).

    Как костюмерная театра: хранит все костюмы (personas),
    позволяет быстро переодеть всех актёров.

    Аргументы:
        persona_file: путь к JSON-файлу с personas
        default_persona: имя persona по умолчанию

    Пример:
        loader = PersonaLoader()
        persona = loader.get_persona("schoolkids")
        prompt = persona["system_prompt_addendum"]
    """

    def __init__(self, persona_file: Path = None, default_persona: str = "schoolkids"):
        self._persona_file = persona_file or (config.DATA_DIR / "personas" / "default_personas.json")
        self._default_persona = default_persona
        self._personas: Dict[str, dict] = {}
        self._current_persona = default_persona
        self._lock = threading.Lock()
        self._last_load_time = 0

        # Создать папку если нет
        self._persona_file.parent.mkdir(parents=True, exist_ok=True)

        # Загрузить
        self._load_personas()

        logger.info("PersonaLoader initialized. Current persona: %s", self._current_persona)

    def _load_personas(self):
        """Загрузить personas из JSON-файла."""
        if not self._persona_file.exists():
            logger.warning("Persona file not found: %s. Using built-in defaults.", self._persona_file)
            self._personas = self._get_builtin_personas()
            self._save_builtin_defaults()
            return

        try:
            with open(self._persona_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Пропускаем _meta
            personas = {k: v for k, v in data.items() if not k.startswith("_")}

            with self._lock:
                self._personas = personas
                self._last_load_time = time.time()

            logger.info("Loaded %d personas from %s", len(personas), self._persona_file)

        except Exception as e:
            logger.error("Failed to load personas: %s", e)
            self._personas = self._get_builtin_personas()

    def _save_builtin_defaults(self):
        """Сохранить встроенные personas в файл для редактирования."""
        try:
            with open(self._persona_file, "w", encoding="utf-8") as f:
                json.dump(self._get_builtin_personas(), f, ensure_ascii=False, indent=2)
            logger.info("Saved default personas to %s", self._persona_file)
        except Exception as e:
            logger.error("Failed to save default personas: %s", e)

    def _get_builtin_personas(self) -> Dict[str, dict]:
        """Встроенные personas (fallback если файл не найден)."""
        return {
            "schoolkids": {
                "name": "Школьники",
                "system_prompt_addendum": "Ты — школьник 15 лет. Говоришь сленгом ('кринж', 'рофл', 'имба').",
                "tactic_acknowledgment_style": "Неформальный, с энтузиазмом.",
                "voice_examples": ["Го, ща разнесём!", "Бет, я готов!"],
            },
            "roleplayers": {
                "name": "Ролеплейщики",
                "system_prompt_addendum": "Ты — житель Азерота. Говоришь ОТ ПЕРВОГО ЛИЦА. Знаешь лор.",
                "tactic_acknowledgment_style": "Военная дисциплина, братство.",
                "voice_examples": ["За честь Орды!", "Мой клинок остер."],
            }
        }

    def get_persona(self, name: str = None) -> dict:
        """
        Получить persona по имени.

        Аргументы:
            name: имя persona (schoolkids, roleplayers, и т.д.)
                  Если None — возвращает текущую активную persona

        Возвращает:
            dict с полями persona или fallback
        """
        persona_name = name or self._current_persona

        with self._lock:
            persona = self._personas.get(persona_name)

        if not persona:
            logger.warning("Persona '%s' not found, using default", persona_name)
            persona = self._personas.get(self._default_persona, {})

        return persona

    def get_current_persona_name(self) -> str:
        """Имя текущей активной persona."""
        return self._current_persona

    def set_persona(self, name: str) -> bool:
        """
        Сменить активную persona.

        Аргументы:
            name: имя persona из файла

        Возвращает:
            True если успешно, False если persona не найдена
        """
        if name not in self._personas:
            logger.error("Cannot set persona '%s': not found", name)
            return False

        self._current_persona = name
        logger.info("Active persona changed to: %s (%s)",
                   name, self._personas[name].get("name", "Unknown"))
        return True

    def reload(self):
        """Перезагрузить personas из файла (горячая перезагрузка)."""
        logger.info("Reloading personas from %s...", self._persona_file)
        self._load_personas()

    def list_personas(self) -> Dict[str, str]:
        """Список доступных personas (имя → описание)."""
        with self._lock:
            return {k: v.get("name", k) for k, v in self._personas.items()}


# ═══════════════════════════════════════════════════════════════════
# ГЛОБАЛЬНЫЙ ЭКЗЕМПЛЯР (singleton)
# ═══════════════════════════════════════════════════════════════════

_persona_loader_instance: Optional[PersonaLoader] = None


def get_persona_loader() -> PersonaLoader:
    """Получить глобальный экземпляр PersonaLoader."""
    global _persona_loader_instance
    if _persona_loader_instance is None:
        _persona_loader_instance = PersonaLoader()
    return _persona_loader_instance


def init_persona_loader(persona_file: Path = None, default: str = "schoolkids") -> PersonaLoader:
    """Инициализировать PersonaLoader с параметрами."""
    global _persona_loader_instance
    _persona_loader_instance = PersonaLoader(persona_file, default)
    return _persona_loader_instance
"""
SOAPBridge — отправка команд ботам через SOAP API AzerothCore

Как это работает (простыми словами):
1. Python формирует XML-запрос с командой (например ".whisper Alfonso attack")
2. Отправляет по HTTP на порт SOAP сервера (обычно 7878)
3. Сервер выполняет команду как будто GM ввёл её в консоль
4. Бот получает шёпот и идёт в атаку

Требует в worldserver.conf:
    SOAP.Enabled = 1
    SOAP.IP = 127.0.0.1
    SOAP.Port = 7878
"""

import requests
import logging
import xml.etree.ElementTree as ET
from typing import Optional, Dict, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SOAPConfig:
    """Настройки подключения к SOAP"""
    host: str = "127.0.0.1"
    port: int = 7878
    username: str = "gm_admin"      # <-- ПОМЕНЯЙ на свой GM-аккаунт
    password: str = "gm_password"   # <-- ПОМЕНЯЙ на свой пароль
    timeout: float = 5.0


class SOAPBridge:
    """
    Мост SOAP для отправки команд серверу и ботам.

    Пример использования:
        soap = SOAPBridge()
        soap.whisper_bot("Alfonso", "attack")     # Бот пойдёт в атаку
        soap.whisper_bot("Alfonso", "follow")     # Бот побежит за тобой
        soap.whisper_bot("Alfonso", "co +tank")   # Сменит стратегию
    """

    def __init__(self, config: SOAPConfig = None):
        self.cfg = config or SOAPConfig()
        self.url = f"http://{self.cfg.host}:{self.cfg.port}/"
        self.auth = (self.cfg.username, self.cfg.password)
        
        # Проверяем подключение при создании
        self._test_connection()
        
        logger.info("SOAPBridge ready: %s (user=%s)", self.url, self.cfg.username)

    def _build_soap_body(self, command: str) -> str:
        """
        Собрать XML для SOAP-запроса.
        Это как "заполнить бланк" — шаблон всегда один, меняется только команда.
        """
        # Экранируем спецсимволы XML
        safe_cmd = command.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ns1="urn:AC">
    <SOAP-ENV:Body>
        <ns1:executeCommand>
            <command>{safe_cmd}</command>
        </ns1:executeCommand>
    </SOAP-ENV:Body>
</SOAP-ENV:Envelope>"""

    def _send(self, command: str) -> tuple[bool, str]:
        """
        Отправить команду через SOAP.
        
        Возвращает:
            (успешно_ли, ответ_сервера)
        """
        body = self._build_soap_body(command)
        
        try:
            resp = requests.post(
                self.url,
                data=body.encode('utf-8'),
                auth=self.auth,
                headers={"Content-Type": "text/xml; charset=utf-8"},
                timeout=self.cfg.timeout
            )
            
            if resp.status_code != 200:
                logger.warning("SOAP HTTP %d: %s", resp.status_code, resp.text[:200])
                return False, f"HTTP {resp.status_code}"
            
            # Парсим ответ — ищем <result> или ошибки
            text = resp.text
            if "<result>" in text:
                # Извлекаем результат
                start = text.find("<result>") + 8
                end = text.find("</result>")
                result = text[start:end] if end > start else "OK"
                return True, result
            
            if "Error" in text or "error" in text:
                return False, text[:300]
            
            return True, "OK"
            
        except requests.Timeout:
            logger.error("SOAP timeout after %.1fs", self.cfg.timeout)
            return False, "timeout"
        except Exception as e:
            logger.error("SOAP request failed: %s", e)
            return False, str(e)

    def _test_connection(self):
        """Проверить, что SOAP работает, командой .server info"""
        ok, result = self._send(".server info")
        if ok:
            logger.info("SOAP connection OK")
        else:
            logger.warning("SOAP test failed: %s", result)
            logger.warning("Проверь: SOAP.Enabled=1 и аккаунт GM в worldserver.conf")

    # ═══════════════════════════════════════════════════════════════
    # ПУБЛИЧНЫЕ МЕТОДЫ: Команды ботам
    # ═══════════════════════════════════════════════════════════════

    def whisper_bot(self, bot_name: str, message: str) -> bool:
        """
        Шепнуть боту команду.
        
        Playerbots реагирует на whisper от мастера.
        Команды: attack, follow, stay, flee, heal, co +tank, etc.
        
        Пример:
            soap.whisper_bot("Alfonso", "attack")
            soap.whisper_bot("Alfonso", "co +tank,-threat")
        """
        # Команда .whisper в AzerothCore: .whisper Имя "Сообщение"
        # Но лучше использовать встроенную команду шепота сервера
        cmd = f'.whisper {bot_name} {message}'
        ok, result = self._send(cmd)
        
        if ok:
            logger.info("→ %s: %s", bot_name, message)
        else:
            logger.error("→ %s FAILED: %s", bot_name, result)
        
        return ok

    def mass_whisper(self, bot_names: List[str], message: str) -> Dict[str, bool]:
        """
        Шепнуть нескольким ботам одну команду.
        Возвращает словарь {имя_бота: успешно_ли}
        """
        results = {}
        for name in bot_names:
            results[name] = self.whisper_bot(name, message)
        return results

    def send_party_command(self, player_name: str, command: str, 
                           target_filter: str = "@all") -> bool:
        """
        Отправить команду в party chat от имени GM.
        
        Хак: используем .nameannounce или .notify? Нет, это для всех.
        Правильно: .send mass message — но это сложно.
        
        Проще: шепчем каждому боту отдельно через mass_whisper.
        """
        # Получаем список ботов в группе игрока — нужен запрос в БД
        # Пока заглушка: используем mass_whisper если список известен
        logger.debug("Party command for %s: %s %s", player_name, target_filter, command)
        return True  # Заглушка — реализуем через БД-запрос

    def set_bot_strategy(self, bot_name: str, strategy: str) -> bool:
        """
        Установить боевую стратегию бота.
        
        strategy: "co +tank,-threat" или "nc +follow,+loot"
        """
        return self.whisper_bot(bot_name, strategy)

    def send_emergency_heal(self, bot_name: str, target_name: str = "leader") -> bool:
        """
        Экстренная команда хилу.
        Максимально быстрая (без лишних проверок).
        """
        # Сначала переключаем стратегию на хил
        self.whisper_bot(bot_name, "co +heal,-dps")
        # Потом явно командуем исцелить
        return self.whisper_bot(bot_name, f"heal {target_name}")

    # ═══════════════════════════════════════════════════════════════
    # ИНТЕГРАЦИЯ С TACTICAL PLANNER
    # ═══════════════════════════════════════════════════════════════

    def execute_tactic_step(self, step: dict) -> bool:
        """
        Выполнить один шаг тактического плана.
        Учитываем, что playerbots — это AI со стратегиями, не пет.
        """
        bot_name = step.get("bot_name")
        action = step.get("action", "follow")
        strategy = step.get("strategy_cmd")
        
        if not bot_name:
            logger.error("No bot_name in tactic step")
            return False

        # Приоритет: стратегия > action
        if strategy and strategy not in ("", "null"):
            return self.whisper_bot(bot_name, strategy)
        
        # Преобразуем action в ПРАВИЛЬНУЮ команду playerbots
        action_map = {
            # Боевые действия → СТРАТЕГИИ (не мгновенные команды!)
            "attack": "co +dps assist",      # Бить цель лидера
            "tank": "co +tank",              # Танковать
            "heal": "co +heal",              # Хилить
            "pull": "co +pull",              # Пуллить
            "aoe": "co +aoe",                # АоЕ
            "cc": "co +cc",                  # Контроль толпы
            
            # Небоевые → мгновенные или стратегии
            "follow": "follow",               # Мгновенно: бежать к лидеру
            "stay": "stay",                  # Мгновенно: стоять
            "flee": "flee",                  # Мгновенно: бежать
            "wait": "stay",                  # Ждать = стоять
        }
        
        cmd = action_map.get(action, action)
        return self.whisper_bot(bot_name, cmd)

    def shutdown(self):
        """Закрыть соединения (requests сам управляет)"""
        logger.info("SOAPBridge shutdown")
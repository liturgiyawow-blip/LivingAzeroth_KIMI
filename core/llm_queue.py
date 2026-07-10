"""
PriorityLLMQueue — приоритетная очередь к LM Studio
Один worker, один запрос к LLM в момент. Без моков, только реальные запросы.
"""

import queue
import threading
import time
import json
import re
from concurrent.futures import Future
from typing import Optional

import requests
import logging

import config

logger = logging.getLogger(__name__)


class PriorityLLMQueue:
    def __init__(self, base_url: str = None):
        self.base_url = base_url or config.LLM_BASE_URL
        self.chat_url = f"{self.base_url}/chat/completions"
        
        self._queue = queue.PriorityQueue()
        self._task_counter = 0
        self._counter_lock = threading.Lock()
        
        self._running = True
        self._worker = threading.Thread(target=self._worker_loop, daemon=True, name="LLMWorker")
        self._worker.start()
        
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})
        
        self._current_task = None
        self._current_lock = threading.Lock()
        
        self._stats = {
            "total_requests": 0,
            "failed_requests": 0,
            "avg_latency_ms": 0.0,
            "priority_1_count": 0,
            "priority_2_count": 0,
            "priority_3_count": 0,
        }
        self._stats_lock = threading.Lock()
        
        logger.info("PriorityLLMQueue initialized. URL: %s", self.chat_url)
    
    def submit(self, system_prompt: str, user_prompt: str, 
               temperature: float = 0.7, max_tokens: int = 150,
               priority: int = 2) -> Future:
        with self._counter_lock:
            self._task_counter += 1
            task_id = self._task_counter
        
        task = {
            "task_id": task_id,
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "priority": priority,
            "future": Future(),
        }
        
        # FIX: PriorityQueue — чем меньше число, тем выше приоритет
        self._queue.put((priority, time.time(), task_id, task))
        logger.debug("Task %d queued (priority %d)", task_id, priority)
        return task["future"]
    
    def _worker_loop(self):
        """
        FIX: убрана багоопасная логика peek в self._queue.queue[0].
        Теперь просто ждём задачу из PriorityQueue — она сама сортирует
        по приоритету. Нет риска IndexError или race condition.
        """
        while self._running:
            try:
                priority, timestamp, task_id, task = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            
            with self._current_lock:
                self._current_task = task
            
            try:
                result = self._execute_task(task)
                task["future"].set_result(result)
            except Exception as e:
                logger.error("Task %d execution failed: %s", task_id, e)
                task["future"].set_exception(e)
            finally:
                with self._current_lock:
                    self._current_task = None
                self._queue.task_done()  # FIX: был утерян, очередь не уменьшалась
    
    def _execute_task(self, task: dict) -> dict:
        payload = {
            "model": "local-model",
            "messages": [
                {"role": "system", "content": task["system_prompt"]},
                {"role": "user", "content": task["user_prompt"]}
            ],
            "temperature": task["temperature"],
            "max_tokens": task["max_tokens"],
            "stream": False,
        }
        
        start_time = time.time()
        priority = task.get("priority", 2)
        
        try:
            logger.info("Sending to LLM (task %d, priority %d)...", 
                       task["task_id"], priority)
            
            resp = self._session.post(
                self.chat_url,
                json=payload,
                timeout=config.LLM_TIMEOUT
            )
            resp.raise_for_status()
            
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            
            latency = (time.time() - start_time) * 1000
            self._update_stats(latency, priority, success=True)
            
            logger.info("LLM responded in %.0f ms", latency)
            return self._safe_parse_json(content)
            
        except requests.Timeout:
            logger.error("LLM timeout after %.0f seconds", config.LLM_TIMEOUT)
            self._update_stats(config.LLM_TIMEOUT * 1000, priority, success=False)
            return {
                "error": "LLM_TIMEOUT",
                "fallback": True,
                "speech": "Мне нужно подумать...",
                "emote_id": 0,
                "mood_change": "0",
                "action_command": None,
                "set_flag": None,
            }
        except Exception as e:
            logger.error("LLM request failed: %s", e)
            self._update_stats(0, priority, success=False)
            return {
                "error": str(e),
                "fallback": True,
                "speech": "Что-то пошло не так...",
                "emote_id": 0,
                "mood_change": "0",
                "action_command": None,
                "set_flag": None,
            }
    
    def _safe_parse_json(self, content: str) -> dict:
        content = content.strip()
        
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()
        
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass
        
        try:
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if match:
                return json.loads(match.group())
        except (ValueError, json.JSONDecodeError):
            pass
        
        logger.warning("JSON parse failed, returning raw text")
        # FIX: возвращаем полный dict с action_command, чтобы validators не падал
        return {
            "speech": content[:500],
            "emote_id": 0,
            "mood_change": "0",
            "set_flag": None,
            "action_command": None,
            "fallback": True,
        }
    
    def _update_stats(self, latency_ms: float, priority: int, success: bool):
        with self._stats_lock:
            self._stats["total_requests"] += 1
            if not success:
                self._stats["failed_requests"] += 1
            
            key = f"priority_{priority}_count"
            if key in self._stats:
                self._stats[key] += 1
            
            self._stats["avg_latency_ms"] = (
                0.9 * self._stats["avg_latency_ms"] + 0.1 * latency_ms
            )
    
    def get_stats(self) -> dict:
        with self._stats_lock:
            return dict(self._stats)
    
    def get_queue_size(self) -> int:
        return self._queue.qsize()
    
    def get_current_task_info(self) -> Optional[dict]:
        with self._current_lock:
            if self._current_task:
                return {
                    "priority": self._current_task.get("priority"),
                    "task_id": self._current_task.get("task_id"),
                    "max_tokens": self._current_task.get("max_tokens"),
                }
            return None
    
    def shutdown(self):
        self._running = False
        self._worker.join(timeout=5.0)
        self._session.close()
        logger.info("PriorityLLMQueue shutdown")
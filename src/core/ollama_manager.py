# -*- coding: utf-8 -*-
"""
Модуль для автоматического управления Ollama сервисом.
Проверяет и запускает Ollama при необходимости.
"""
import subprocess
import requests
import time
import platform
import logging
from typing import Optional, Callable

logger = logging.getLogger(__name__)


class OllamaManager:
    """Менеджер для автоматического управления Ollama"""
    
    def __init__(self, progress_callback: Optional[Callable[[str], None]] = None):
        self.progress_callback = progress_callback or (lambda msg: None)
        self.ollama_url = "http://localhost:11434"
    
    def _log(self, message: str):
        """Логирование"""
        self.progress_callback(message)
        logger.info(message)
    
    def _check_ollama_running(self) -> bool:
        """Проверяет, запущен ли Ollama"""
        try:
            response = requests.get(f"{self.ollama_url}/api/tags", timeout=2)
            return response.status_code == 200
        except:
            return False
    
    def _start_ollama_service(self) -> bool:
        """Запускает Ollama через brew services (Mac)"""
        system = platform.system()
        
        if system == "Darwin":  # macOS
            try:
                # Проверяем, установлен ли brew
                result = subprocess.run(
                    ["which", "brew"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                
                if result.returncode != 0:
                    self._log("⚠️ Homebrew не найден. Ollama нужно запустить вручную.")
                    return False
                
                # Запускаем Ollama через brew services
                self._log("🚀 Запуск Ollama через brew services...")
                result = subprocess.run(
                    ["brew", "services", "start", "ollama"],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                
                if result.returncode == 0:
                    self._log("✅ Команда запуска Ollama выполнена")
                    return True
                else:
                    self._log(f"⚠️ Ошибка запуска Ollama: {result.stderr}")
                    return False
                    
            except subprocess.TimeoutExpired:
                self._log("⚠️ Таймаут при запуске Ollama")
                return False
            except FileNotFoundError:
                self._log("⚠️ Команда brew не найдена")
                return False
            except Exception as e:
                self._log(f"⚠️ Ошибка при запуске Ollama: {e}")
                return False
        else:
            # Для других ОС (Windows, Linux) - просто проверяем
            self._log("ℹ️ Автоматический запуск Ollama поддерживается только на macOS")
            self._log("💡 Запустите Ollama вручную для вашей ОС")
            return False
    
    def ensure_ollama_running(self, auto_start: bool = True) -> bool:
        """
        Убеждается, что Ollama запущен.
        
        Args:
            auto_start: Автоматически запускать Ollama, если он не запущен
            
        Returns:
            True если Ollama запущен и доступен, False иначе
        """
        # Проверяем, запущен ли Ollama
        if self._check_ollama_running():
            self._log("✅ Ollama уже запущен")
            return True
        
        # Если не запущен и разрешен автозапуск
        if auto_start:
            self._log("⚠️ Ollama не запущен. Попытка автоматического запуска...")
            
            if self._start_ollama_service():
                # Ждем, пока Ollama запустится
                self._log("⏳ Ожидание запуска Ollama...")
                for i in range(10):  # Максимум 10 попыток (10 секунд)
                    time.sleep(1)
                    if self._check_ollama_running():
                        self._log("✅ Ollama успешно запущен!")
                        return True
                    self._log(f"⏳ Попытка {i+1}/10...")
                
                self._log("⚠️ Ollama не ответил после запуска. Проверьте вручную.")
                return False
            else:
                self._log("💡 Запустите Ollama вручную: brew services start ollama")
                return False
        else:
            self._log("⚠️ Ollama не запущен")
            self._log("💡 Запустите Ollama вручную: brew services start ollama")
            return False
    
    def check_model_available(self, model_name: str = "qwen2.5:7b") -> bool:
        """
        Проверяет, установлена ли модель в Ollama.
        
        Args:
            model_name: Название модели для проверки
            
        Returns:
            True если модель доступна, False иначе
        """
        if not self._check_ollama_running():
            return False
        
        try:
            response = requests.get(f"{self.ollama_url}/api/tags", timeout=5)
            if response.status_code == 200:
                models = response.json().get("models", [])
                model_names = [m.get("name", "") for m in models]
                return any(model_name in name for name in model_names)
        except:
            pass
        
        return False

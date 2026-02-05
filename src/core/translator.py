# -*- coding: utf-8 -*-
"""
Модуль перевода сегментов транскрипции.
Поддерживает Ollama (локальный LLM) и deep-translator/googletrans как резервный вариант.
"""
import json
import requests
import time
import subprocess
import sys
from typing import List, Dict, Optional, Callable
from concurrent.futures import ThreadPoolExecutor
import logging

# Логирование
logger = logging.getLogger(__name__)

class Translator:
    """
    Класс для перевода сегментов транскрипции с сохранением временных меток и спикеров.
    """
    
    def __init__(
        self,
        ollama_url: str = "http://localhost:11434",
        progress_callback: Optional[Callable[[str], None]] = None,
        should_stop_callback: Optional[Callable[[], bool]] = None
    ):
        """
        Инициализация переводчика.
        
        Args:
            ollama_url: URL Ollama сервера (по умолчанию http://localhost:11434)
            progress_callback: Функция для отчета о прогрессе (принимает строку сообщения)
        """
        self.ollama_url = ollama_url.rstrip('/')
        self.progress_callback = progress_callback or (lambda msg: None)
        self.should_stop_callback = should_stop_callback
        self._ollama_available = None  # Кэш для проверки доступности Ollama
        self._deep_translator_installed = None  # Кэш для проверки установки deep-translator
        
    def _log(self, message: str):
        """Внутренний метод для логирования"""
        self.progress_callback(message)
        logger.info(message)
    
    def _check_deep_translator_installed(self) -> bool:
        """
        Проверяет, установлен ли deep-translator.
        
        Returns:
            True если установлен, False иначе
        """
        if self._deep_translator_installed is not None:
            return self._deep_translator_installed
        
        try:
            import deep_translator
            self._deep_translator_installed = True
            return True
        except ImportError:
            self._deep_translator_installed = False
            return False
    
    def _install_deep_translator(self) -> bool:
        """
        Устанавливает deep-translator через pip.
        
        Returns:
            True если установка успешна, False иначе
        """
        try:
            self._log("📦 Установка deep-translator...")
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "deep-translator"],
                capture_output=True,
                text=True,
                timeout=120
            )
            
            if result.returncode == 0:
                self._log("✅ deep-translator успешно установлен")
                self._deep_translator_installed = True
                return True
            else:
                self._log(f"❌ Ошибка установки deep-translator: {result.stderr}")
                return False
        except subprocess.TimeoutExpired:
            self._log("❌ Таймаут при установке deep-translator")
            return False
        except Exception as e:
            self._log(f"❌ Ошибка при установке deep-translator: {e}")
            return False
    
    def _check_ollama_available(self) -> bool:
        """
        Проверяет доступность Ollama сервера.
        
        Returns:
            True если Ollama доступен, False иначе
        """
        if self._ollama_available is not None:
            return self._ollama_available
        
        try:
            response = requests.get(f"{self.ollama_url}/api/tags", timeout=2)
            self._ollama_available = response.status_code == 200
            return self._ollama_available
        except (requests.exceptions.RequestException, requests.exceptions.Timeout):
            self._ollama_available = False
            return False
    
    def _get_ollama_models(self) -> List[str]:
        """
        Получает список доступных моделей Ollama.
        
        Returns:
            Список названий моделей
        """
        try:
            response = requests.get(f"{self.ollama_url}/api/tags", timeout=5)
            if response.status_code == 200:
                data = response.json()
                models = [model['name'] for model in data.get('models', [])]
                return models
        except Exception as e:
            logger.warning(f"Не удалось получить список моделей Ollama: {e}")
        
        # Возвращаем список моделей по умолчанию
        return ["qwen2.5:7b", "llama3", "llama3.1", "mistral", "phi3"]
    
    def _translate_with_ollama(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        model: str = "qwen2.5:7b"
    ) -> str:
        """
        Переводит текст с помощью Ollama.
        
        Args:
            text: Текст для перевода
            source_lang: Исходный язык (например, "en", "ru")
            target_lang: Целевой язык (например, "ru", "en")
            model: Название модели Ollama
            
        Returns:
            Переведенный текст
        """
        # Определяем полные названия языков для промпта
        lang_names = {
            "en": "English",
            "ru": "Russian",
            "es": "Spanish",
            "fr": "French",
            "de": "German",
            "it": "Italian",
            "pt": "Portuguese",
            "ja": "Japanese",
            "ko": "Korean",
            "zh": "Chinese",
        }
        
        source_lang_name = lang_names.get(source_lang.lower(), source_lang)
        target_lang_name = lang_names.get(target_lang.lower(), target_lang)
        
        # Критически важный промпт для профессионального перевода
        system_prompt = (
            f"You are a professional dubbing translator. "
            f"Translate the following text from {source_lang_name} to {target_lang_name}. "
            f"Keep the translation concise and matching the original length/duration. "
            f"Do NOT output any explanations, just the translated text."
        )
        
        user_prompt = text
        
        # Используем /api/chat для более стабильной работы
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "stream": False,
            "options": {
                "temperature": 0.3,  # Низкая температура для более точного перевода
            }
        }
        
        try:
            response = requests.post(
                f"{self.ollama_url}/api/chat",
                json=payload,
                timeout=60
            )
            response.raise_for_status()
            
            data = response.json()
            translated_text = data.get("message", {}).get("content", "").strip()
            
            # Если ответ пустой, пробуем старый API /api/generate
            if not translated_text:
                return self._translate_with_ollama_generate(text, source_lang, target_lang, model)
            
            return translated_text
            
        except requests.exceptions.Timeout:
            self._log(f"⚠️ Таймаут при обращении к Ollama. Пробуем резервный метод...")
            raise
        except Exception as e:
            logger.error(f"Ошибка перевода через Ollama: {e}")
            raise
    
    def _translate_with_ollama_generate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        model: str = "qwen2.5:7b"
    ) -> str:
        """
        Резервный метод перевода через /api/generate (старый API).
        """
        lang_names = {
            "en": "English",
            "ru": "Russian",
            "es": "Spanish",
            "fr": "French",
            "de": "German",
            "it": "Italian",
            "pt": "Portuguese",
            "ja": "Japanese",
            "ko": "Korean",
            "zh": "Chinese",
        }
        
        source_lang_name = lang_names.get(source_lang.lower(), source_lang)
        target_lang_name = lang_names.get(target_lang.lower(), target_lang)
        
        prompt = (
            f"You are a professional dubbing translator. "
            f"Translate the following text from {source_lang_name} to {target_lang_name}. "
            f"Keep the translation concise and matching the original length/duration. "
            f"Do NOT output any explanations, just the translated text.\n\n"
            f"Text: {text}"
        )
        
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.3,
            }
        }
        
        response = requests.post(
            f"{self.ollama_url}/api/generate",
            json=payload,
            timeout=60
        )
        response.raise_for_status()
        
        data = response.json()
        translated_text = data.get("response", "").strip()
        
        return translated_text
    
    def _translate_with_deepl(self, text: str, source_lang: str, target_lang: str) -> Optional[str]:
        """
        Переводит текст с помощью DeepL (самое качество).
        
        Args:
            text: Текст для перевода
            source_lang: Исходный язык
            target_lang: Целевой язык
            
        Returns:
            Переведенный текст или None если недоступен
        """
        try:
            from deep_translator import DeeplTranslator
        except ImportError:
            # Пытаемся установить библиотеку автоматически
            if not self._check_deep_translator_installed():
                self._install_deep_translator()
                try:
                    from deep_translator import DeeplTranslator
                except ImportError:
                    return None
            else:
                return None
        
        try:
            # DeepL требует API ключ, пропускаем если его нет
            # Проверяем наличие ключа в переменных окружения
            import os
            if not os.getenv('DEEPL_API_KEY'):
                logger.debug("DeepL пропущен: требуется API ключ")
                return None
            
            # DeepL использует коды языков в формате "EN", "RU" и т.д.
            lang_map = {
                "en": "EN", "ru": "RU", "es": "ES", "fr": "FR", "de": "DE",
                "it": "IT", "pt": "PT", "ja": "JA", "ko": "KO", "zh": "ZH"
            }
            source_deepl = lang_map.get(source_lang.lower(), source_lang.upper())
            target_deepl = lang_map.get(target_lang.lower(), target_lang.upper())
            
            # Пытаемся использовать DeepL с API ключом
            translator = DeeplTranslator(source=source_deepl, target=target_deepl)
            return translator.translate(text)
        except Exception as e:
            error_msg = str(e)
            # Если ошибка связана с отсутствием API ключа - просто пропускаем
            if "api_key" in error_msg.lower() or "DEEPL_API_KEY" in error_msg or "You have to pass your api_key" in error_msg:
                logger.debug("DeepL пропущен: требуется API ключ")
                return None
            logger.debug(f"DeepL недоступен: {e}")
            return None
    
    def _translate_with_libretranslate(self, text: str, source_lang: str, target_lang: str) -> Optional[str]:
        """
        Переводит текст с помощью LibreTranslate (open-source, качественный).
        
        Args:
            text: Текст для перевода
            source_lang: Исходный язык
            target_lang: Целевой язык
            
        Returns:
            Переведенный текст или None если недоступен
        """
        try:
            from deep_translator import LibreTranslator
        except ImportError:
            # Пытаемся установить библиотеку автоматически
            if not self._check_deep_translator_installed():
                self._install_deep_translator()
                try:
                    from deep_translator import LibreTranslator
                except ImportError:
                    return None
            else:
                return None
        
        try:
            translator = LibreTranslator(source=source_lang, target=target_lang)
            result = translator.translate(text)
            if result and result.strip():
                return result
            return None
        except Exception as e:
            error_msg = str(e)
            logger.debug(f"LibreTranslate недоступен: {error_msg}")
            return None
    
    def _translate_with_mymemory(self, text: str, source_lang: str, target_lang: str) -> Optional[str]:
        """
        Переводит текст с помощью MyMemory (качественный бесплатный API).
        
        Args:
            text: Текст для перевода
            source_lang: Исходный язык
            target_lang: Целевой язык
            
        Returns:
            Переведенный текст или None если недоступен
        """
        try:
            from deep_translator import MyMemoryTranslator
        except ImportError:
            # Пытаемся установить библиотеку автоматически
            if not self._check_deep_translator_installed():
                self._install_deep_translator()
                try:
                    from deep_translator import MyMemoryTranslator
                except ImportError:
                    return None
            else:
                return None
        
        try:
            translator = MyMemoryTranslator(source=source_lang, target=target_lang)
            result = translator.translate(text)
            if result and result.strip():
                return result
            return None
        except Exception as e:
            error_msg = str(e)
            logger.debug(f"MyMemory недоступен: {error_msg}")
            return None
    
    def _translate_with_google(self, text: str, source_lang: str, target_lang: str) -> Optional[str]:
        """
        Переводит текст с помощью Google Translate (fallback).
        
        Args:
            text: Текст для перевода
            source_lang: Исходный язык
            target_lang: Целевой язык
            
        Returns:
            Переведенный текст или None если недоступен
        """
        try:
            from deep_translator import GoogleTranslator
        except ImportError:
            # Пытаемся установить библиотеку автоматически
            if not self._check_deep_translator_installed():
                self._install_deep_translator()
                try:
                    from deep_translator import GoogleTranslator
                except ImportError:
                    return None
            else:
                return None
        
        try:
            translator = GoogleTranslator(source=source_lang, target=target_lang)
            result = translator.translate(text)
            if result and result.strip():
                return result
            return None
        except Exception as e:
            error_msg = str(e)
            logger.debug(f"Google Translate недоступен: {error_msg}")
            return None
    
    def _translate_with_fallback(self, text: str, source_lang: str, target_lang: str) -> str:
        """
        Переводит текст с помощью лучшего доступного метода.
        Приоритет: DeepL > LibreTranslate > MyMemory > Google Translate > googletrans.
        Если все методы недоступны — возвращает исходный текст (пайплайн не прерывается).
        """
        providers = [
            ("DeepL", self._translate_with_deepl),
            ("LibreTranslate", self._translate_with_libretranslate),
            ("MyMemory", self._translate_with_mymemory),
            ("Google Translate", self._translate_with_google),
        ]
        errors_list = []
        for provider_name, translate_func in providers:
            for attempt in range(2):  # до 2 попыток с задержкой
                try:
                    result = translate_func(text, source_lang, target_lang)
                    if result and result.strip():
                        return result
                    errors_list.append(f"{provider_name} вернул пустой результат")
                except Exception as e:
                    errors_list.append(f"{provider_name}: {str(e)[:80]}")
                if attempt == 0:
                    time.sleep(0.5)
                continue
            time.sleep(0.3)

        # Последний резерв: googletrans
        try:
            from googletrans import Translator as GoogleTrans
            trans = GoogleTrans()
            result = trans.translate(text, src=source_lang, dest=target_lang)
            if result and getattr(result, "text", None) and str(result.text).strip():
                return str(result.text).strip()
        except Exception as e:
            errors_list.append(f"googletrans: {str(e)[:80]}")

        # Ни один провайдер не сработал — возвращаем исходный текст, пайплайн не падает
        logger.warning(
            "Перевод недоступен (сеть/лимиты?), оставляем оригинал. Ошибки: %s",
            "; ".join(errors_list[:4]) + (" ..." if len(errors_list) > 4 else ""),
        )
        return text
    
    def _detect_language(self, segments: List[Dict]) -> str:
        """
        Определяет язык текста по сегментам (простая эвристика).
        
        Args:
            segments: Список сегментов
            
        Returns:
            Код языка (например, "en", "ru")
        """
        if not segments:
            return "en"
        
        # Берем первые несколько сегментов для анализа
        sample_text = " ".join([seg.get("text", "") for seg in segments[:5]])
        
        # Простая эвристика: проверяем наличие кириллицы
        if any('\u0400' <= char <= '\u04FF' for char in sample_text):
            return "ru"
        
        # По умолчанию английский
        return "en"
    
    def translate_segments(
        self,
        segments: List[Dict],
        target_lang: str = "ru",
        source_lang: Optional[str] = None,
        model: str = "qwen2.5:7b",
        use_fallback: bool = True,
        force_fallback: bool = False,
        batch_size: int = 1
    ) -> List[Dict]:
        """
        Переводит список сегментов, сохраняя временные метки и спикеров.
        
        Args:
            segments: Список сегментов в формате [{"start": 10.5, "end": 15.2, "text": "...", "speaker": "SPEAKER_01"}, ...]
            target_lang: Целевой язык (по умолчанию "ru")
            source_lang: Исходный язык (если None, определяется автоматически)
            model: Название модели Ollama (используется только если Ollama доступен)
            use_fallback: Использовать резервный метод перевода, если Ollama недоступен
            force_fallback: Принудительно использовать только резервный метод (игнорировать Ollama)
            batch_size: Размер батча для перевода (1 = по одному сегменту)
            
        Returns:
            Новый список сегментов с переведенным текстом
        """
        if not segments:
            self._log("ℹ️ Список сегментов пуст")
            return []
        
        # Валидация входных данных
        if not isinstance(segments, list):
            raise TypeError("segments должен быть списком")
        
        if batch_size < 1:
            batch_size = 1
        
        # Определяем исходный язык
        if source_lang is None:
            source_lang = self._detect_language(segments)
            self._log(f"🌍 Определен язык: {source_lang}")
        
        # Нормализуем языки для сравнения (приводим к нижнему регистру)
        source_lang_normalized = source_lang.lower() if source_lang else None
        target_lang_normalized = target_lang.lower() if target_lang else None
        
        if source_lang_normalized == target_lang_normalized:
            self._log(f"ℹ️ Исходный ({source_lang}) и целевой ({target_lang}) языки совпадают, перевод не требуется")
            return segments
        
        # По умолчанию используем качественные API вместо Ollama
        # Ollama используется только если явно запрошен и доступен
        if force_fallback:
            use_ollama = False
            self._log("🌐 Используется качественный API перевод (DeepL/LibreTranslate/MyMemory/Google)")
        else:
            # Проверяем доступность Ollama, но не делаем его приоритетным
            ollama_available = self._check_ollama_available()
            # По умолчанию используем API, но если пользователь хочет Ollama - используем
            use_ollama = False  # Отключаем Ollama по умолчанию для лучшего качества
            if ollama_available:
                self._log("💡 Ollama доступен, но используется качественный API перевод для лучшего результата")
        
        if use_ollama:
            self._log(f"🤖 Используется Ollama (модель: {model})")
        elif use_fallback:
            self._log("🌐 Используется качественный API перевод (DeepL → LibreTranslate → MyMemory → Google)")
        else:
            raise RuntimeError(
                "Не удалось инициализировать перевод. "
                "Установите: pip install deep-translator"
            )
        
        # Создаем копию сегментов для перевода
        translated_segments = []
        total = len(segments)
        
        # Обрабатываем сегменты батчами или по одному
        for i in range(0, total, batch_size):
            # Проверяем флаг остановки в цикле
            if self.should_stop_callback and self.should_stop_callback():
                self._log("⏹️ Перевод прерван пользователем")
                raise InterruptedError("Processing stopped by user")
            
            batch = segments[i:i + batch_size]
            batch_num = (i // batch_size) + 1
            total_batches = (total + batch_size - 1) // batch_size
            
            for seg_idx, segment in enumerate(batch):
                # Проверяем флаг остановки в цикле
                if self.should_stop_callback and self.should_stop_callback():
                    self._log("⏹️ Перевод прерван пользователем")
                    raise InterruptedError("Processing stopped by user")
                
                current_idx = i + seg_idx + 1
                self._log(f"🔄 Перевод сегмента {current_idx}/{total}...")
                
                text = segment.get("text", "").strip()
                
                if not text:
                    # Если текста нет, просто копируем сегмент
                    translated_segments.append(segment.copy())
                    continue
                
                try:
                    # Переводим текст
                    if use_ollama:
                        translated_text = self._translate_with_ollama(
                            text, source_lang, target_lang, model
                        )
                    else:
                        translated_text = self._translate_with_fallback(
                            text, source_lang, target_lang
                        )
                    
                    # Создаем новый сегмент с переведенным текстом
                    new_segment = segment.copy()
                    new_segment["text"] = translated_text
                    translated_segments.append(new_segment)
                    
                except InterruptedError:
                    self._log("⏹️ Перевод прерван пользователем")
                    raise
                except RuntimeError as e:
                    error_msg = str(e)
                    # Проверяем, связана ли ошибка с отсутствием deep-translator
                    if "deep-translator" in error_msg.lower() or "установите: pip install" in error_msg.lower():
                        # Проверяем, установлен ли deep-translator
                        if not self._check_deep_translator_installed():
                            # Пытаемся установить
                            if self._install_deep_translator():
                                # Повторяем попытку перевода
                                try:
                                    self._log(f"🔄 Повторная попытка перевода сегмента {current_idx}...")
                                    if use_ollama:
                                        translated_text = self._translate_with_ollama(
                                            text, source_lang, target_lang, model
                                        )
                                    else:
                                        translated_text = self._translate_with_fallback(
                                            text, source_lang, target_lang
                                        )
                                    new_segment = segment.copy()
                                    new_segment["text"] = translated_text
                                    translated_segments.append(new_segment)
                                    # Успешно переведено, переходим к следующему сегменту
                                    continue
                                except Exception as retry_error:
                                    self._log(f"❌ Повторная попытка не удалась: {retry_error}")
                                    # Оставляем оригинальный текст
                                    translated_segments.append(segment.copy())
                            else:
                                # Установка не удалась
                                self._log(f"❌ Не удалось установить deep-translator. Оставляем оригинальный текст.")
                                translated_segments.append(segment.copy())
                        else:
                            # Библиотека уже установлена, но все равно ошибка
                            self._log(f"⚠️ Ошибка перевода сегмента {current_idx}: {error_msg}")
                            translated_segments.append(segment.copy())
                    else:
                        # Другая ошибка
                        self._log(f"⚠️ Ошибка перевода сегмента {current_idx}: {error_msg}")
                        # Если Ollama не сработал, пробуем резервный метод
                        if use_ollama and use_fallback:
                            try:
                                self._log(f"🔄 Пробуем резервный метод для сегмента {current_idx}...")
                                translated_text = self._translate_with_fallback(
                                    text, source_lang, target_lang
                                )
                                new_segment = segment.copy()
                                new_segment["text"] = translated_text
                                translated_segments.append(new_segment)
                            except Exception as fallback_error:
                                self._log(f"❌ Резервный метод тоже не сработал: {fallback_error}")
                                # Оставляем оригинальный текст
                                translated_segments.append(segment.copy())
                        else:
                            # Оставляем оригинальный текст
                            translated_segments.append(segment.copy())
                except Exception as e:
                    self._log(f"⚠️ Ошибка перевода сегмента {current_idx}: {str(e)}")
                    # Если Ollama не сработал, пробуем резервный метод
                    if use_ollama and use_fallback:
                        try:
                            self._log(f"🔄 Пробуем резервный метод для сегмента {current_idx}...")
                            translated_text = self._translate_with_fallback(
                                text, source_lang, target_lang
                            )
                            new_segment = segment.copy()
                            new_segment["text"] = translated_text
                            translated_segments.append(new_segment)
                        except Exception as fallback_error:
                            self._log(f"❌ Резервный метод тоже не сработал: {fallback_error}")
                            # Оставляем оригинальный текст
                            translated_segments.append(segment.copy())
                    else:
                        # Оставляем оригинальный текст
                        translated_segments.append(segment.copy())
            
            # Небольшая задержка между батчами, чтобы не перегружать API
            if i + batch_size < total:
                time.sleep(0.1)
        
        self._log(f"✅ Перевод завершен: {len(translated_segments)} сегментов")
        return translated_segments

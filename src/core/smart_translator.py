# -*- coding: utf-8 -*-
"""
Smart Translator — профессиональный перевод с учётом тайминга для дубляжа.

Ключевые особенности:
- Перевод через локальный LLM (Ollama) с учётом ограничения по длительности
- Оценка длительности речи до генерации TTS
- Итеративное уточнение перевода, если текст не умещается
- Сохранение смысла при сокращении текста
"""
import json
import requests
import time
import logging
from typing import List, Dict, Optional, Callable, Tuple

logger = logging.getLogger(__name__)

# Константы для оценки длительности речи (символов в секунду)
# Эти значения откалиброваны для XTTS v2 при speed=1.0
SPEECH_RATE = {
    'ru': 13.0,    # Русский: ~13 символов/сек
    'en': 14.5,    # Английский: ~14.5 символов/сек
    'es': 14.0,    # Испанский
    'fr': 14.0,    # Французский
    'de': 13.0,    # Немецкий
    'it': 14.0,    # Итальянский
    'pt': 14.0,    # Португальский
    'ja': 8.0,     # Японский (меньше символов, но сложнее)
    'ko': 10.0,    # Корейский
    'zh': 6.0,     # Китайский (иероглифы)
    'ar': 12.0,    # Арабский
    'hi': 12.0,    # Хинди
    'pl': 13.0,    # Польский
    'tr': 13.0,    # Турецкий
    'nl': 14.0,    # Голландский
    'cs': 13.0,    # Чешский
    'hu': 13.0,    # Венгерский
}

# Максимальное ускорение TTS (больше = хуже качество)
MAX_TTS_SPEED = 1.15
# Максимальное ускорение при пост-обработке atempo
MAX_ATEMPO_SPEED = 1.10


class SmartTranslator:
    """
    Профессиональный переводчик для дубляжа с учётом временных ограничений.
    """

    def __init__(
        self,
        ollama_url: str = "http://localhost:11434",
        model: str = "qwen2.5:7b",
        progress_callback: Optional[Callable[[str], None]] = None,
        should_stop_callback: Optional[Callable[[], bool]] = None
    ):
        self.ollama_url = ollama_url.rstrip('/')
        self.model = model
        self.progress_callback = progress_callback or (lambda msg: None)
        self.should_stop_callback = should_stop_callback
        self._ollama_available = None

    def _log(self, message: str):
        self.progress_callback(message)
        logger.info(message)

    def _check_ollama_available(self) -> bool:
        """Проверяет доступность Ollama"""
        if self._ollama_available is not None:
            return self._ollama_available

        try:
            response = requests.get(f"{self.ollama_url}/api/tags", timeout=3)
            self._ollama_available = response.status_code == 200
            return self._ollama_available
        except:
            self._ollama_available = False
            return False

    def estimate_speech_duration(self, text: str, language: str) -> float:
        """
        Оценивает длительность речи для данного текста.

        Args:
            text: Текст для оценки
            language: Код языка (ru, en, etc.)

        Returns:
            Оценочная длительность в секундах
        """
        lang = language.lower()[:2]
        chars_per_sec = SPEECH_RATE.get(lang, 13.0)

        # Считаем только значимые символы (без пробелов по краям)
        clean_text = text.strip()

        # Учитываем паузы на знаках препинания
        pause_chars = clean_text.count('.') + clean_text.count('!') + clean_text.count('?')
        pause_chars += clean_text.count(',') * 0.3 + clean_text.count(';') * 0.5

        # Базовая длительность + паузы (0.2 сек на точку, 0.1 на запятую)
        base_duration = len(clean_text) / chars_per_sec
        pause_duration = pause_chars * 0.15

        return base_duration + pause_duration

    def calculate_max_chars(self, duration_sec: float, language: str, speed: float = 1.0) -> int:
        """
        Вычисляет максимальное количество символов для заданной длительности.

        Args:
            duration_sec: Доступная длительность в секундах
            language: Код языка
            speed: Фактор скорости TTS (1.0 = нормально, 1.15 = быстрее)

        Returns:
            Максимальное количество символов
        """
        lang = language.lower()[:2]
        chars_per_sec = SPEECH_RATE.get(lang, 13.0)

        # С учётом скорости можно уместить больше символов
        effective_chars_per_sec = chars_per_sec * speed

        # Оставляем 10% запас на паузы
        max_chars = int(duration_sec * effective_chars_per_sec * 0.9)

        return max(max_chars, 10)  # Минимум 10 символов

    def _call_ollama(self, messages: List[Dict], temperature: float = 0.3) -> str:
        """Вызывает Ollama API"""
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": 500,  # Ограничиваем ответ
            }
        }

        response = requests.post(
            f"{self.ollama_url}/api/chat",
            json=payload,
            timeout=60
        )
        response.raise_for_status()

        data = response.json()
        return data.get("message", {}).get("content", "").strip()

    def translate_with_timing(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        target_duration_sec: float,
        max_iterations: int = 3
    ) -> Tuple[str, float]:
        """
        Переводит текст с учётом временного ограничения.

        Args:
            text: Исходный текст
            source_lang: Исходный язык
            target_lang: Целевой язык
            target_duration_sec: Целевая длительность в секундах
            max_iterations: Максимум итераций уточнения

        Returns:
            Tuple[переведённый_текст, рекомендуемая_скорость_TTS]
        """
        lang_names = {
            "en": "English", "ru": "Russian", "es": "Spanish", "fr": "French",
            "de": "German", "it": "Italian", "pt": "Portuguese", "ja": "Japanese",
            "ko": "Korean", "zh": "Chinese", "ar": "Arabic", "hi": "Hindi",
            "pl": "Polish", "tr": "Turkish", "nl": "Dutch", "cs": "Czech", "hu": "Hungarian"
        }

        source_name = lang_names.get(source_lang.lower()[:2], source_lang)
        target_name = lang_names.get(target_lang.lower()[:2], target_lang)
        target_lang_code = target_lang.lower()[:2]

        # Вычисляем лимит символов для нормальной скорости
        max_chars_normal = self.calculate_max_chars(target_duration_sec, target_lang_code, speed=1.0)
        # И для максимально допустимой скорости
        max_chars_fast = self.calculate_max_chars(target_duration_sec, target_lang_code, speed=MAX_TTS_SPEED)

        # Первая попытка: перевод с мягким ограничением
        system_prompt = f"""You are a professional dubbing translator. Your task is to translate dialogue for voice dubbing.

CRITICAL RULES:
1. Translate from {source_name} to {target_name}
2. The translation MUST be spoken in {target_duration_sec:.1f} seconds
3. Aim for approximately {max_chars_normal} characters (MAXIMUM {max_chars_fast} characters)
4. Keep the MEANING and EMOTION intact
5. Use natural spoken language, not written/formal style
6. If the original is too long, rephrase concisely while preserving meaning
7. Output ONLY the translated text, nothing else"""

        user_prompt = f"Translate this dialogue line (must fit in {target_duration_sec:.1f} seconds, max {max_chars_fast} chars):\n\n{text}"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        translated = self._call_ollama(messages, temperature=0.3)

        # Убираем возможные кавычки и лишние символы
        translated = translated.strip().strip('"\'')

        # Проверяем длительность перевода
        estimated_duration = self.estimate_speech_duration(translated, target_lang_code)

        # Если перевод умещается при нормальной скорости - отлично
        if estimated_duration <= target_duration_sec:
            return translated, 1.0

        # Если умещается при небольшом ускорении - используем его
        speed_needed = estimated_duration / target_duration_sec
        if speed_needed <= MAX_TTS_SPEED:
            return translated, min(speed_needed, MAX_TTS_SPEED)

        # Нужно сократить перевод - итеративное уточнение
        for iteration in range(max_iterations):
            current_chars = len(translated)
            target_chars = int(max_chars_normal * 0.95)  # Чуть меньше лимита

            refine_prompt = f"""The translation is too long for dubbing. Current: {current_chars} chars, need: {target_chars} chars max.

Original meaning: "{text}"
Current translation: "{translated}"

Rewrite the translation to be SHORTER while keeping the same meaning. Use fewer words, simpler phrases.
Target: maximum {target_chars} characters.
Output ONLY the shortened translation."""

            messages = [
                {"role": "system", "content": f"You are a professional dubbing translator. Shorten the {target_name} text while preserving meaning."},
                {"role": "user", "content": refine_prompt}
            ]

            refined = self._call_ollama(messages, temperature=0.4)
            refined = refined.strip().strip('"\'')

            # Проверяем новую версию
            new_duration = self.estimate_speech_duration(refined, target_lang_code)
            new_speed = new_duration / target_duration_sec

            if new_speed <= MAX_TTS_SPEED:
                return refined, min(new_speed, MAX_TTS_SPEED)

            # Если стало короче - продолжаем с новой версией
            if len(refined) < len(translated):
                translated = refined
            else:
                # Не удалось сократить - используем принудительную обрезку смысла
                break

        # Финальная попытка: жёсткое ограничение
        final_prompt = f"""CRITICAL: The text MUST be maximum {max_chars_normal} characters. This is a hard limit.

Original meaning: "{text}"

Write a {target_name} translation that:
1. Is MAXIMUM {max_chars_normal} characters (count carefully!)
2. Captures the ESSENCE of the original
3. Sounds natural when spoken

If you cannot fit everything, keep only the most important part.
Output ONLY the translation, nothing else."""

        messages = [
            {"role": "system", "content": f"You are a professional dubbing translator. Output only {target_name} text."},
            {"role": "user", "content": final_prompt}
        ]

        final = self._call_ollama(messages, temperature=0.5)
        final = final.strip().strip('"\'')

        # Если всё ещё слишком длинный - обрезаем по словам
        if len(final) > max_chars_fast:
            words = final.split()
            truncated = ""
            for word in words:
                if len(truncated) + len(word) + 1 > max_chars_fast:
                    break
                truncated += (" " if truncated else "") + word
            final = truncated.rstrip(',;:') + "..."

        final_duration = self.estimate_speech_duration(final, target_lang_code)
        final_speed = min(final_duration / target_duration_sec, MAX_TTS_SPEED)

        return final, max(final_speed, 1.0)

    def translate_segments(
        self,
        segments: List[Dict],
        target_lang: str = "ru",
        source_lang: Optional[str] = None
    ) -> List[Dict]:
        """
        Переводит список сегментов с учётом тайминга каждого.

        Args:
            segments: Список сегментов [{start, end, text, speaker}, ...]
            target_lang: Целевой язык
            source_lang: Исходный язык (автоопределение если None)

        Returns:
            Список сегментов с переводом и рекомендуемой скоростью TTS
        """
        if not segments:
            self._log("ℹ️ Список сегментов пуст")
            return []

        if not self._check_ollama_available():
            raise RuntimeError(
                "Ollama не доступен. Запустите: brew services start ollama\n"
                "Или установите: brew install ollama && ollama pull qwen2.5:7b"
            )

        # Автоопределение языка
        if source_lang is None:
            sample = " ".join([s.get("text", "") for s in segments[:5]])
            source_lang = "ru" if any('\u0400' <= c <= '\u04FF' for c in sample) else "en"
            self._log(f"🌍 Определён язык: {source_lang}")

        # Проверяем, нужен ли перевод
        if source_lang.lower()[:2] == target_lang.lower()[:2]:
            self._log(f"ℹ️ Языки совпадают ({source_lang}), перевод не требуется")
            for seg in segments:
                seg['tts_speed'] = 1.0
            return segments

        self._log(f"🎯 Умный перевод: {source_lang} → {target_lang} ({len(segments)} сегментов)")
        self._log(f"🤖 Модель: {self.model}")

        translated_segments = []
        total = len(segments)

        for i, seg in enumerate(segments):
            if self.should_stop_callback and self.should_stop_callback():
                self._log("⏹️ Перевод прерван")
                raise InterruptedError("Processing stopped by user")

            text = seg.get("text", "").strip()
            start = float(seg.get("start", 0))
            end = float(seg.get("end", start + 1))
            duration = end - start

            self._log(f"🔄 [{i+1}/{total}] Перевод ({duration:.1f}s): {text[:50]}...")

            if not text:
                new_seg = seg.copy()
                new_seg['tts_speed'] = 1.0
                translated_segments.append(new_seg)
                continue

            try:
                translated_text, tts_speed = self.translate_with_timing(
                    text=text,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    target_duration_sec=duration
                )

                new_seg = seg.copy()
                new_seg['text'] = translated_text
                new_seg['original_text'] = text
                new_seg['tts_speed'] = tts_speed

                est_duration = self.estimate_speech_duration(translated_text, target_lang)
                self._log(f"   ✅ {len(translated_text)} симв., ~{est_duration:.1f}s, speed={tts_speed:.2f}x")

                translated_segments.append(new_seg)

            except Exception as e:
                self._log(f"   ⚠️ Ошибка перевода: {e}, оставляем оригинал")
                new_seg = seg.copy()
                new_seg['tts_speed'] = 1.0
                translated_segments.append(new_seg)

            # Небольшая пауза между запросами
            time.sleep(0.1)

        self._log(f"✅ Умный перевод завершён: {len(translated_segments)} сегментов")
        return translated_segments


# Обратная совместимость — функция для вызова из api_server
def smart_translate_segments(
    segments: List[Dict],
    target_lang: str = "ru",
    source_lang: Optional[str] = None,
    ollama_url: str = "http://localhost:11434",
    model: str = "qwen2.5:7b",
    progress_callback: Optional[Callable[[str], None]] = None,
    should_stop_callback: Optional[Callable[[], bool]] = None
) -> List[Dict]:
    """
    Функция-обёртка для умного перевода сегментов.
    """
    translator = SmartTranslator(
        ollama_url=ollama_url,
        model=model,
        progress_callback=progress_callback,
        should_stop_callback=should_stop_callback
    )
    return translator.translate_segments(segments, target_lang, source_lang)

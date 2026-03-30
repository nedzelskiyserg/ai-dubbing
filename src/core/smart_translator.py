# -*- coding: utf-8 -*-
"""
Smart Translator — профессиональный перевод с учётом тайминга для дубляжа.

Ключевые особенности:
- Batch-перевод: несколько сегментов за один вызов LLM (5-10x быстрее)
- Поддержка Ollama (локально) и OpenRouter (облако, Gemini Flash и др.)
- Оценка длительности речи до генерации TTS
- Итеративное уточнение только для сегментов с превышением бюджета
- Сохранение смысла при сокращении текста
"""
import json
import os
import re
import random
import requests
import requests.exceptions
import time
import logging
from typing import List, Dict, Optional, Callable, Tuple
from core.elastic_timing import compute_effective_durations

logger = logging.getLogger(__name__)

# Константы для оценки длительности речи (символов в секунду)
# Откалибровано для F5-TTS (flow matching, nfe_step=48).
SPEECH_RATE = {
    'ru': 11.5,    # Русский: ~11.5 символов/сек (F5-TTS + RUAccent)
    'en': 13.0,    # Английский: ~13 символов/сек
    'es': 12.5,    # Испанский
    'fr': 12.5,    # Французский
    'de': 11.5,    # Немецкий
    'it': 12.5,    # Итальянский
    'pt': 12.5,    # Португальский
    'ja': 7.0,     # Японский (иероглифы + кана)
    'ko': 9.0,     # Корейский
    'zh': 5.5,     # Китайский (иероглифы)
    'ar': 11.0,    # Арабский
    'hi': 11.0,    # Хинди
    'pl': 11.5,    # Польский
    'tr': 12.0,    # Турецкий
    'nl': 12.5,    # Голландский
    'cs': 11.5,    # Чешский
    'hu': 11.5,    # Венгерский
    'sv': 12.5,    # Шведский
    'no': 12.5,    # Норвежский
}

MAX_TTS_SPEED = 1.10
MAX_ATEMPO_SPEED = 1.10

# Batch-перевод: сколько сегментов переводить за один вызов LLM
BATCH_SIZE_OLLAMA = 6
BATCH_SIZE_OPENROUTER = 12  # Облачные модели быстрее — можно больший batch

# OpenRouter API
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_DEFAULT_MODEL = "google/gemini-3-flash-preview"

# Retry configuration for transient API errors
API_MAX_RETRIES = 4
API_BASE_DELAY = 1.0    # seconds
API_MAX_DELAY = 30.0    # seconds

# Transient errors worth retrying
_TRANSIENT_EXCEPTIONS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)


class SmartTranslator:
    """
    Профессиональный переводчик для дубляжа с учётом временных ограничений.
    Batch-режим: переводит несколько сегментов за один вызов LLM.
    Поддерживает Ollama (локально) и OpenRouter (облако).
    """

    def __init__(
        self,
        ollama_url: str = "http://localhost:11434",
        model: str = "qwen2.5:7b",
        provider: str = "ollama",
        api_key: Optional[str] = None,
        progress_callback: Optional[Callable[[str], None]] = None,
        should_stop_callback: Optional[Callable[[], bool]] = None
    ):
        self.provider = provider.lower()  # 'ollama' or 'openrouter'
        self.ollama_url = ollama_url.rstrip('/')
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        self.progress_callback = progress_callback or (lambda msg: None)
        self.should_stop_callback = should_stop_callback
        self._ollama_available = None

        # Выбираем модель в зависимости от провайдера
        if self.provider == "openrouter":
            self.model = model if model != "qwen2.5:7b" else OPENROUTER_DEFAULT_MODEL
            self.batch_size = BATCH_SIZE_OPENROUTER
        else:
            self.model = model
            self.batch_size = BATCH_SIZE_OLLAMA

    def _log(self, message: str):
        self.progress_callback(message)
        logger.info(message)

    def _check_provider_available(self) -> bool:
        """Проверяет доступность выбранного провайдера."""
        if self.provider == "openrouter":
            if not self.api_key:
                return False
            return True  # Проверка ключа будет при первом вызове

        # Ollama
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
        lang = language.lower()[:2]
        chars_per_sec = SPEECH_RATE.get(lang, 13.0)
        clean_text = text.strip()
        pause_chars = clean_text.count('.') + clean_text.count('!') + clean_text.count('?')
        pause_chars += clean_text.count(',') * 0.3 + clean_text.count(';') * 0.5
        base_duration = len(clean_text) / chars_per_sec
        pause_duration = pause_chars * 0.15
        return base_duration + pause_duration

    def calculate_max_chars(self, duration_sec: float, language: str, speed: float = 1.0) -> int:
        lang = language.lower()[:2]
        chars_per_sec = SPEECH_RATE.get(lang, 12.0)
        effective_chars_per_sec = chars_per_sec * speed
        max_chars = int(duration_sec * effective_chars_per_sec * 0.9)
        return max(max_chars, 10)

    def _call_llm(self, messages: List[Dict], temperature: float = 0.3,
                  num_predict: int = 500) -> str:
        """Единый диспетчер вызовов LLM — Ollama или OpenRouter."""
        if self.provider == "openrouter":
            return self._call_openrouter(messages, temperature, num_predict)
        return self._call_ollama(messages, temperature, num_predict)

    def _call_ollama(self, messages: List[Dict], temperature: float = 0.3,
                     num_predict: int = 500) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": num_predict,
            }
        }
        last_exception = None
        for attempt in range(API_MAX_RETRIES + 1):
            try:
                response = requests.post(
                    f"{self.ollama_url}/api/chat",
                    json=payload,
                    timeout=120
                )
                if response.status_code >= 500:
                    if attempt < API_MAX_RETRIES:
                        delay = self._backoff_delay(attempt)
                        self._log(f"   ⏳ Ollama {response.status_code}, retry {attempt + 1}/{API_MAX_RETRIES} через {delay:.1f}s...")
                        time.sleep(delay)
                        continue
                response.raise_for_status()
                data = response.json()
                return data.get("message", {}).get("content", "").strip()
            except _TRANSIENT_EXCEPTIONS as e:
                last_exception = e
                if attempt < API_MAX_RETRIES:
                    delay = self._backoff_delay(attempt)
                    self._log(f"   ⏳ Ollama {type(e).__name__}, retry {attempt + 1}/{API_MAX_RETRIES} через {delay:.1f}s...")
                    time.sleep(delay)
                    continue
                raise
        raise last_exception  # pragma: no cover

    def _call_openrouter(self, messages: List[Dict], temperature: float = 0.3,
                         num_predict: int = 500) -> str:
        """Вызов OpenRouter API (OpenAI-compatible) с retry и exponential backoff."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/nedzelskiyserg/ai-dubbing",
            "X-Title": "AI Dubbing Studio"
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": num_predict,
        }

        last_exception = None
        for attempt in range(API_MAX_RETRIES + 1):
            try:
                response = requests.post(
                    OPENROUTER_API_URL,
                    headers=headers,
                    json=payload,
                    timeout=120
                )
                # Retry on 429 (rate limit) and 5xx (server errors)
                if response.status_code == 429 or response.status_code >= 500:
                    retry_after = response.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after else self._backoff_delay(attempt)
                    if attempt < API_MAX_RETRIES:
                        self._log(f"   ⏳ API {response.status_code}, retry {attempt + 1}/{API_MAX_RETRIES} через {delay:.1f}s...")
                        time.sleep(delay)
                        continue
                    response.raise_for_status()

                response.raise_for_status()
                data = response.json()
                choices = data.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", "").strip()
                return ""

            except _TRANSIENT_EXCEPTIONS as e:
                last_exception = e
                if attempt < API_MAX_RETRIES:
                    delay = self._backoff_delay(attempt)
                    self._log(f"   ⏳ {type(e).__name__}, retry {attempt + 1}/{API_MAX_RETRIES} через {delay:.1f}s...")
                    time.sleep(delay)
                    continue
                raise

        raise last_exception  # pragma: no cover

    @staticmethod
    def _backoff_delay(attempt: int) -> float:
        """Exponential backoff with jitter."""
        delay = min(API_BASE_DELAY * (2 ** attempt), API_MAX_DELAY)
        return delay + random.uniform(0, delay * 0.5)

    @staticmethod
    def _has_chinese_chars(text: str) -> bool:
        return bool(re.search(r'[\u4e00-\u9fff\u3400-\u4dbf]', text))

    # ── Batch-перевод ────────────────────────────────────────────────

    def _translate_batch(
        self,
        batch: List[Dict],
        source_lang: str,
        target_lang: str,
    ) -> List[str]:
        """
        Переводит пакет сегментов одним вызовом LLM.
        Возвращает список переводов (в том же порядке).
        """
        lang_names = {
            "en": "English", "ru": "Russian", "es": "Spanish", "fr": "French",
            "de": "German", "it": "Italian", "pt": "Portuguese", "ja": "Japanese",
            "ko": "Korean", "zh": "Chinese", "ar": "Arabic", "hi": "Hindi",
            "pl": "Polish", "tr": "Turkish", "nl": "Dutch", "cs": "Czech", "hu": "Hungarian",
            "sv": "Swedish", "no": "Norwegian"
        }
        source_name = lang_names.get(source_lang.lower()[:2], source_lang)
        target_name = lang_names.get(target_lang.lower()[:2], target_lang)
        target_lang_code = target_lang.lower()[:2]

        no_chinese_rule = ""
        if target_lang_code not in ("zh", "ja", "ko"):
            no_chinese_rule = f"\nNEVER use Chinese characters. Output ONLY {target_name} characters."

        # Формируем список строк для перевода
        lines = []
        for idx, seg in enumerate(batch):
            text = seg.get("text", "").strip()
            dur = float(seg.get("effective_duration", 3.0))
            max_chars = self.calculate_max_chars(dur, target_lang_code, speed=MAX_TTS_SPEED)
            lines.append(f'{idx + 1}. [{max_chars} chars, {dur:.1f}s] "{text}"')

        lines_block = "\n".join(lines)

        system_prompt = f"""You are a professional dubbing translator ({source_name} → {target_name}).

RULES:
1. Translate each numbered line separately
2. Each line has a character limit [N chars] and time limit [Xs] — respect them
3. Keep MEANING and EMOTION intact
4. Use natural spoken language
5. Output ONLY a JSON array of translated strings, in the same order
6. Example output: ["translation 1", "translation 2", "translation 3"]{no_chinese_rule}"""

        user_prompt = f"Translate these {len(batch)} dialogue lines:\n\n{lines_block}\n\nOutput JSON array:"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        # Увеличиваем num_predict для batch-ответа
        num_predict = max(500, len(batch) * 200)
        raw = self._call_llm(messages, temperature=0.3, num_predict=num_predict)

        # Парсим JSON из ответа
        translations = self._parse_batch_response(raw, len(batch))

        # Валидация: китайские символы
        if target_lang_code not in ("zh", "ja", "ko"):
            for i, t in enumerate(translations):
                if self._has_chinese_chars(t):
                    translations[i] = ""  # Помечаем для fallback

        return translations

    def _parse_batch_response(self, raw: str, expected_count: int) -> List[str]:
        """
        Парсит JSON-массив из ответа LLM.
        Устойчив к мусору вокруг JSON.
        """
        # Ищем JSON-массив в ответе
        match = re.search(r'\[.*\]', raw, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
                if isinstance(parsed, list) and len(parsed) == expected_count:
                    return [str(item).strip().strip('"\'') for item in parsed]
            except json.JSONDecodeError:
                pass

        # Fallback: пробуем построчный парсинг (1. "...", 2. "..." и т.д.)
        results = []
        for i in range(1, expected_count + 1):
            pattern = rf'{i}\.\s*["\']?(.+?)["\']?\s*$'
            line_match = re.search(pattern, raw, re.MULTILINE)
            if line_match:
                results.append(line_match.group(1).strip().strip('"\''))
            else:
                results.append("")  # Не нашли — пометим для fallback

        if len(results) == expected_count and all(results):
            return results

        # Совсем fallback: возвращаем пустые строки для повторного перевода по одному
        return [""] * expected_count

    # ── Одиночный перевод (для refinement и fallback) ────────────────

    def translate_with_timing(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        target_duration_sec: float,
        max_iterations: int = 2
    ) -> Tuple[str, float]:
        lang_names = {
            "en": "English", "ru": "Russian", "es": "Spanish", "fr": "French",
            "de": "German", "it": "Italian", "pt": "Portuguese", "ja": "Japanese",
            "ko": "Korean", "zh": "Chinese", "ar": "Arabic", "hi": "Hindi",
            "pl": "Polish", "tr": "Turkish", "nl": "Dutch", "cs": "Czech", "hu": "Hungarian",
            "sv": "Swedish", "no": "Norwegian"
        }
        source_name = lang_names.get(source_lang.lower()[:2], source_lang)
        target_name = lang_names.get(target_lang.lower()[:2], target_lang)
        target_lang_code = target_lang.lower()[:2]

        no_chinese_rule = ""
        if target_lang_code not in ("zh", "ja", "ko"):
            no_chinese_rule = f"\n8. NEVER use Chinese characters. Output ONLY {target_name} characters."

        max_chars_normal = self.calculate_max_chars(target_duration_sec, target_lang_code, speed=1.0)
        max_chars_fast = self.calculate_max_chars(target_duration_sec, target_lang_code, speed=MAX_TTS_SPEED)

        system_prompt = f"""You are a professional dubbing translator. Translate from {source_name} to {target_name}.

CRITICAL RULES:
1. The translation MUST be spoken in {target_duration_sec:.1f} seconds
2. Aim for {max_chars_normal} characters (MAXIMUM {max_chars_fast} characters)
3. Keep the MEANING and EMOTION intact
4. Use natural spoken language
5. If too long, rephrase concisely
6. Output ONLY the translated text{no_chinese_rule}"""

        user_prompt = f"Translate (max {max_chars_fast} chars):\n\n{text}"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        translated = self._call_llm(messages, temperature=0.3)
        translated = translated.strip().strip('"\'')

        if target_lang_code not in ("zh", "ja", "ko") and self._has_chinese_chars(translated):
            messages_retry = [
                {"role": "system", "content": f"Translate to {target_name} ONLY. No Chinese characters."},
                {"role": "user", "content": f"Translate to {target_name}:\n\n{text}"}
            ]
            translated = self._call_llm(messages_retry, temperature=0.1)
            translated = translated.strip().strip('"\'')

        estimated_duration = self.estimate_speech_duration(translated, target_lang_code)
        if estimated_duration <= target_duration_sec:
            return translated, 1.0

        speed_needed = estimated_duration / target_duration_sec
        if speed_needed <= MAX_TTS_SPEED:
            return translated, min(speed_needed, MAX_TTS_SPEED)

        # Refinement: сокращаем
        for iteration in range(max_iterations):
            target_chars = int(max_chars_normal * 0.95)
            refine_prompt = (
                f"Too long ({len(translated)} chars). Shorten to max {target_chars} chars, "
                f"keep meaning.\n\nOriginal: \"{text}\"\nCurrent: \"{translated}\"\n\n"
                f"Output ONLY the shortened {target_name} translation."
            )
            messages = [
                {"role": "system", "content": f"Shorten the {target_name} dubbing text. Output ONLY {target_name}."},
                {"role": "user", "content": refine_prompt}
            ]
            refined = self._call_llm(messages, temperature=0.4)
            refined = refined.strip().strip('"\'')

            if target_lang_code not in ("zh", "ja", "ko") and self._has_chinese_chars(refined):
                refined = translated

            new_duration = self.estimate_speech_duration(refined, target_lang_code)
            new_speed = new_duration / target_duration_sec
            if new_speed <= MAX_TTS_SPEED:
                return refined, min(new_speed, MAX_TTS_SPEED)
            if len(refined) < len(translated):
                translated = refined
            else:
                break

        # Обрезка по словам если всё ещё слишком длинный
        if len(translated) > max_chars_fast:
            words = translated.split()
            truncated = ""
            for word in words:
                if len(truncated) + len(word) + 1 > max_chars_fast:
                    break
                truncated += (" " if truncated else "") + word
            translated = truncated.rstrip(',;:') + "..."

        final_duration = self.estimate_speech_duration(translated, target_lang_code)
        final_speed = min(final_duration / target_duration_sec, MAX_TTS_SPEED)
        return translated, max(final_speed, 1.0)

    # ── Основной метод ──────────────────────────────────────────────

    def translate_segments(
        self,
        segments: List[Dict],
        target_lang: str = "ru",
        source_lang: Optional[str] = None
    ) -> List[Dict]:
        if not segments:
            self._log("ℹ️ Список сегментов пуст")
            return []

        if not self._check_provider_available():
            if self.provider == "openrouter":
                raise RuntimeError(
                    "OpenRouter API ключ не указан. Получите ключ на https://openrouter.ai/keys\n"
                    "Укажите его в настройках или в переменной окружения OPENROUTER_API_KEY"
                )
            import sys
            if sys.platform == 'win32':
                raise RuntimeError(
                    "Ollama не доступен. Скачайте и установите Ollama с https://ollama.com\n"
                    "После установки запустите: ollama pull qwen2.5:7b"
                )
            else:
                raise RuntimeError(
                    "Ollama не доступен. Запустите: brew services start ollama\n"
                    "Или установите: brew install ollama && ollama pull qwen2.5:7b"
                )

        if source_lang is None:
            sample = " ".join([s.get("text", "") for s in segments[:5]])
            source_lang = "ru" if any('\u0400' <= c <= '\u04FF' for c in sample) else "en"
            self._log(f"🌍 Определён язык: {source_lang}")

        if source_lang.lower()[:2] == target_lang.lower()[:2]:
            self._log(f"ℹ️ Языки совпадают ({source_lang}), перевод не требуется")
            for seg in segments:
                seg['tts_speed'] = 1.0
            return segments

        target_lang_code = target_lang.lower()[:2]

        provider_label = "OpenRouter" if self.provider == "openrouter" else "Ollama"
        self._log(f"🎯 Batch-перевод: {source_lang} → {target_lang} ({len(segments)} сегментов, batch={self.batch_size})")
        self._log(f"🤖 {provider_label}: {self.model}")

        # Elastic Timing
        compute_effective_durations(segments, total_duration_sec=None)
        extensions = [s.get('gap_extension', 0) for s in segments if s.get('gap_extension', 0) > 0]
        if extensions:
            self._log(
                f"   Elastic timing: {len(extensions)} сегментов с расширением, "
                f"среднее +{sum(extensions)/len(extensions):.2f}s"
            )

        # Разделяем на непустые и пустые
        indexed_segments = []  # (original_index, seg) для сегментов с текстом
        translated_segments = [None] * len(segments)
        total = len(segments)

        for i, seg in enumerate(segments):
            text = seg.get("text", "").strip()
            if not text:
                new_seg = seg.copy()
                new_seg['tts_speed'] = 1.0
                translated_segments[i] = new_seg
            else:
                indexed_segments.append((i, seg))

        if not indexed_segments:
            return [s for s in translated_segments if s is not None]

        # ── Batch-перевод ───────────────────────────────────────────
        batches = []
        for b_start in range(0, len(indexed_segments), self.batch_size):
            batches.append(indexed_segments[b_start:b_start + self.batch_size])

        self._log(f"📦 {len(batches)} batch(ей) по {self.batch_size} сегментов")

        processed = 0
        for batch_idx, batch in enumerate(batches):
            if self.should_stop_callback and self.should_stop_callback():
                self._log("⏹️ Перевод прерван")
                raise InterruptedError("Processing stopped by user")

            batch_segs = [seg for _, seg in batch]
            batch_indices = [idx for idx, _ in batch]

            self._log(f"🔄 Batch {batch_idx + 1}/{len(batches)} ({len(batch)} сегментов)...")
            start_time = time.time()

            try:
                translations = self._translate_batch(batch_segs, source_lang, target_lang)
            except Exception as e:
                logger.warning(f"Batch translation failed: {e}, falling back to single mode")
                translations = [""] * len(batch)

            batch_time = time.time() - start_time
            self._log(f"   ⏱️ Batch за {batch_time:.1f}s")

            # Обрабатываем результаты
            need_refinement = []

            for j, (orig_idx, seg) in enumerate(batch):
                translated_text = translations[j] if j < len(translations) else ""

                if not translated_text:
                    # Fallback: переводим по одному
                    need_refinement.append((orig_idx, seg, "empty"))
                    continue

                effective_duration = float(seg.get("effective_duration", 3.0))
                est_duration = self.estimate_speech_duration(translated_text, target_lang_code)
                speed_needed = est_duration / effective_duration if effective_duration > 0 else 1.0

                if speed_needed <= MAX_TTS_SPEED:
                    # Перевод укладывается в бюджет
                    tts_speed = max(speed_needed, 1.0) if speed_needed > 1.0 else 1.0
                    new_seg = seg.copy()
                    new_seg['text'] = translated_text
                    new_seg['original_text'] = seg.get("text", "").strip()
                    new_seg['tts_speed'] = tts_speed
                    new_seg['effective_duration'] = effective_duration
                    new_seg['gap_extension'] = float(seg.get("gap_extension", 0.0))
                    translated_segments[orig_idx] = new_seg
                    processed += 1
                else:
                    # Превышает бюджет — нужен refinement
                    need_refinement.append((orig_idx, seg, translated_text))

            # Refinement для сегментов с превышением бюджета (по одному)
            for orig_idx, seg, initial_text in need_refinement:
                if self.should_stop_callback and self.should_stop_callback():
                    self._log("⏹️ Перевод прерван")
                    raise InterruptedError("Processing stopped by user")

                text = seg.get("text", "").strip()
                effective_duration = float(seg.get("effective_duration", 3.0))

                try:
                    if initial_text == "empty":
                        # Полный перевод по одному (fallback от batch)
                        translated_text, tts_speed = self.translate_with_timing(
                            text, source_lang, target_lang, effective_duration
                        )
                    else:
                        # Есть batch-перевод, но слишком длинный — сокращаем
                        max_chars_normal = self.calculate_max_chars(effective_duration, target_lang_code, speed=1.0)
                        target_chars = int(max_chars_normal * 0.95)
                        lang_names = {
                            "en": "English", "ru": "Russian", "es": "Spanish",
                            "fr": "French", "de": "German", "it": "Italian",
                            "pt": "Portuguese", "ja": "Japanese", "ko": "Korean",
                            "zh": "Chinese", "ar": "Arabic", "hi": "Hindi",
                            "pl": "Polish", "tr": "Turkish", "nl": "Dutch",
                            "cs": "Czech", "hu": "Hungarian", "sv": "Swedish",
                            "no": "Norwegian"
                        }
                        target_name = lang_names.get(target_lang_code, target_lang)

                        refine_prompt = (
                            f"Too long ({len(initial_text)} chars). Shorten to max {target_chars} chars, "
                            f"keep meaning.\n\nOriginal: \"{text}\"\nCurrent: \"{initial_text}\"\n\n"
                            f"Output ONLY the shortened {target_name} translation."
                        )
                        messages = [
                            {"role": "system", "content": f"Shorten the {target_name} dubbing text. Output ONLY {target_name}."},
                            {"role": "user", "content": refine_prompt}
                        ]
                        refined = self._call_llm(messages, temperature=0.4)
                        refined = refined.strip().strip('"\'')

                        if target_lang_code not in ("zh", "ja", "ko") and self._has_chinese_chars(refined):
                            refined = initial_text

                        est_dur = self.estimate_speech_duration(refined, target_lang_code)
                        speed_needed = est_dur / effective_duration
                        if speed_needed <= MAX_TTS_SPEED and len(refined) < len(initial_text):
                            translated_text = refined
                            tts_speed = min(speed_needed, MAX_TTS_SPEED) if speed_needed > 1.0 else 1.0
                        else:
                            # Берём как есть — atempo справится или voice_cloner подгонит
                            translated_text = initial_text if initial_text != "empty" else refined
                            tts_speed = min(speed_needed, MAX_TTS_SPEED)

                    new_seg = seg.copy()
                    new_seg['text'] = translated_text
                    new_seg['original_text'] = text
                    new_seg['tts_speed'] = tts_speed
                    new_seg['effective_duration'] = effective_duration
                    new_seg['gap_extension'] = float(seg.get("gap_extension", 0.0))
                    translated_segments[orig_idx] = new_seg
                    processed += 1

                except Exception as e:
                    self._log(f"   ⚠️ Ошибка перевода сегмента {orig_idx}: {e}")
                    new_seg = seg.copy()
                    new_seg['tts_speed'] = 1.0
                    translated_segments[orig_idx] = new_seg
                    processed += 1

            self._log(f"   ✅ Batch {batch_idx + 1}: {processed}/{total} сегментов готово")

        # Заполняем оставшиеся None (на случай пропусков)
        for i in range(len(translated_segments)):
            if translated_segments[i] is None:
                new_seg = segments[i].copy()
                new_seg['tts_speed'] = 1.0
                translated_segments[i] = new_seg

        self._log(f"✅ Batch-перевод завершён: {total} сегментов")
        return translated_segments

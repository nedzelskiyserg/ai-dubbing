# -*- coding: utf-8 -*-
"""
OpenAI GPT-4o Transcribe+Diarize — транскрипция и диаризация через OpenAI API.

Использует модель gpt-4o-transcribe-diarize, которая делает
транскрипцию + определение спикеров за один вызов API.

Лимит OpenAI: 25 MB на файл. Для больших файлов аудио конвертируется в mp3.
"""

import os
import json
import subprocess
import tempfile
import math
from typing import Optional, Callable, Dict, List

import requests

from core.config import APP_PATHS


class OpenAITranscriber:
    """Транскрипция + диаризация через OpenAI gpt-4o-transcribe-diarize."""

    API_URL = "https://api.openai.com/v1/audio/transcriptions"
    MODEL = "gpt-4o-transcribe-diarize"
    MAX_FILE_SIZE = 24 * 1024 * 1024  # 24 MB (с запасом от 25 MB лимита)
    MAX_DURATION = 1300  # секунд (с запасом от 1400s лимита модели)

    def __init__(
        self,
        api_key: str,
        progress_callback: Optional[Callable] = None,
        should_stop_callback: Optional[Callable] = None,
    ):
        self.api_key = api_key
        self._log = progress_callback or (lambda msg: None)
        self.should_stop_callback = should_stop_callback

    # ------------------------------------------------------------------
    # Публичный метод — совместим по формату с Transcriber.transcribe_full
    # ------------------------------------------------------------------
    def transcribe_full(
        self,
        audio_path: str,
        language: Optional[str] = None,
        num_speakers: Optional[int] = None,
        **_kwargs,
    ) -> Dict:
        """
        Транскрибирует аудио/видео через OpenAI API.

        Возвращает:
            {"segments": [...], "language": "en"}
        где каждый сегмент: {"start", "end", "text", "speaker"}
        """
        try:
            self._log("\n🎧 Транскрипция через OpenAI (gpt-4o-transcribe-diarize)...")

            # Шаг 1: подготовить аудио (конвертация в mp3 если нужно)
            audio_file = self._prepare_audio(audio_path)

            if self.should_stop_callback and self.should_stop_callback():
                self._cleanup(audio_file, audio_path)
                return {"segments": [], "language": "en", "stopped": True}

            # Шаг 2: вызвать API (возможно по частям)
            file_size = os.path.getsize(audio_file)
            duration = self._get_duration(audio_file)
            needs_chunking = file_size > self.MAX_FILE_SIZE or (duration and duration > self.MAX_DURATION)
            if needs_chunking:
                reason = []
                if file_size > self.MAX_FILE_SIZE:
                    reason.append(f"размер {file_size / 1024 / 1024:.1f} MB > 24 MB")
                if duration and duration > self.MAX_DURATION:
                    reason.append(f"длительность {duration:.0f}s > {self.MAX_DURATION}s")
                self._log(f"📦 {', '.join(reason)} — разбиваю на части...")
                raw_result = self._transcribe_chunked(audio_file, language, duration)
            else:
                raw_result = self._call_api(audio_file, language)

            self._cleanup(audio_file, audio_path)

            if self.should_stop_callback and self.should_stop_callback():
                return {"segments": [], "language": "en", "stopped": True}

            # Шаг 3: конвертировать формат OpenAI → наш внутренний
            segments = self._convert_segments(raw_result)
            detected_lang = raw_result.get("language", language or "en")

            self._log(f"🌍 Язык: {detected_lang}")
            speakers = set(s.get("speaker") for s in segments if s.get("speaker"))
            self._log(f"✅ Готово! Спикеров: {len(speakers)}. Сегментов: {len(segments)}")

            return {"segments": segments, "language": detected_lang}

        except InterruptedError:
            self._log("⏹️ Транскрипция прервана пользователем")
            return {"segments": [], "language": "en", "stopped": True}
        except Exception as e:
            self._log(f"❌ Ошибка OpenAI: {e}")
            import traceback
            self._log(traceback.format_exc())
            return {"segments": [], "language": "en", "error": str(e)}

    # ------------------------------------------------------------------
    # Подготовка аудио
    # ------------------------------------------------------------------
    def _prepare_audio(self, path: str) -> str:
        """Конвертирует видео/wav в mp3 для уменьшения размера."""
        ext = os.path.splitext(path)[1].lower()
        # Если уже mp3 и размер ок — возвращаем как есть
        if ext == ".mp3" and os.path.getsize(path) <= self.MAX_FILE_SIZE:
            return path

        self._log("⚙️ Конвертация аудио в mp3...")
        temp_dir = str(APP_PATHS.get("temp", tempfile.gettempdir()))
        os.makedirs(temp_dir, exist_ok=True)
        mp3_path = os.path.join(temp_dir, "openai_audio.mp3")

        cmd = [
            "ffmpeg", "-y", "-i", path,
            "-vn",           # без видео
            "-acodec", "libmp3lame",
            "-ar", "16000",  # 16kHz — достаточно для речи
            "-ac", "1",      # моно
            "-b:a", "64k",   # 64kbps — компактно, качество ОК для речи
            mp3_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg error: {result.stderr[:500]}")

        size_mb = os.path.getsize(mp3_path) / 1024 / 1024
        self._log(f"✅ Аудио: {size_mb:.1f} MB (mp3, 16kHz mono)")
        return mp3_path

    def _cleanup(self, audio_file: str, original_path: str):
        """Удаляет временный mp3 если он отличается от оригинала."""
        if audio_file != original_path:
            try:
                os.unlink(audio_file)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Вызов API
    # ------------------------------------------------------------------
    def _call_api(self, audio_path: str, language: Optional[str] = None) -> Dict:
        """Один вызов OpenAI Audio API."""
        self._log("📡 Отправка в OpenAI API...")

        headers = {"Authorization": f"Bearer {self.api_key}"}

        import json as _json
        data = {
            "model": self.MODEL,
            "response_format": "diarized_json",
            "chunking_strategy": _json.dumps({"type": "server_vad"}),
        }
        if language:
            data["language"] = language

        with open(audio_path, "rb") as f:
            files = {"file": (os.path.basename(audio_path), f, "audio/mpeg")}
            resp = requests.post(
                self.API_URL,
                headers=headers,
                data=data,
                files=files,
                timeout=600,
            )

        if resp.status_code != 200:
            error_detail = resp.text[:500]
            raise RuntimeError(
                f"OpenAI API error {resp.status_code}: {error_detail}"
            )

        result = resp.json()
        # Логируем структуру для отладки
        keys = list(result.keys())
        self._log(f"✅ API ответил. Ключи: {keys}")
        if result.get("segments"):
            self._log(f"   Сегментов: {len(result['segments'])}")
            # Покажем первый сегмент для понимания формата
            first = result["segments"][0]
            self._log(f"   Пример: {json.dumps(first, ensure_ascii=False)[:300]}")
        if result.get("text"):
            self._log(f"   Текст: {result['text'][:200]}...")
        return result

    # ------------------------------------------------------------------
    # Определение длительности аудио
    # ------------------------------------------------------------------
    def _get_duration(self, audio_path: str) -> Optional[float]:
        """Возвращает длительность аудио в секундах."""
        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "json", audio_path],
                capture_output=True, text=True, timeout=30,
            )
            return float(json.loads(probe.stdout)["format"]["duration"])
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Chunked transcription для файлов > 25 MB или > 1400s
    # ------------------------------------------------------------------
    def _transcribe_chunked(
        self, audio_path: str, language: Optional[str] = None,
        duration: Optional[float] = None,
    ) -> Dict:
        """Разбивает аудио на части и транскрибирует каждую."""
        if duration is None:
            duration = self._get_duration(audio_path)
        if not duration:
            raise RuntimeError("Не удалось определить длительность аудио")

        # Рассчитываем количество частей (по размеру И длительности)
        file_size = os.path.getsize(audio_path)
        chunks_by_size = math.ceil(file_size / self.MAX_FILE_SIZE)
        chunks_by_duration = math.ceil(duration / self.MAX_DURATION)
        num_chunks = max(chunks_by_size, chunks_by_duration)
        chunk_duration = duration / num_chunks

        self._log(f"📦 Разбивка: {num_chunks} частей по ~{chunk_duration:.0f}s")

        temp_dir = str(APP_PATHS.get("temp", tempfile.gettempdir()))
        all_segments = []
        detected_language = language

        for i in range(num_chunks):
            if self.should_stop_callback and self.should_stop_callback():
                raise InterruptedError("Processing stopped by user")

            start_time = i * chunk_duration
            chunk_path = os.path.join(temp_dir, f"openai_chunk_{i}.mp3")

            # Вырезаем часть
            cmd = [
                "ffmpeg", "-y", "-i", audio_path,
                "-ss", str(start_time),
                "-t", str(chunk_duration),
                "-acodec", "libmp3lame",
                "-ar", "16000", "-ac", "1", "-b:a", "64k",
                chunk_path,
            ]
            subprocess.run(cmd, capture_output=True, text=True, timeout=120)

            self._log(f"📡 Часть {i + 1}/{num_chunks}...")
            try:
                chunk_result = self._call_api(chunk_path, language)

                if not detected_language and chunk_result.get("language"):
                    detected_language = chunk_result["language"]

                # Сдвигаем таймкоды на offset
                for seg in chunk_result.get("segments", []):
                    seg["start"] = seg.get("start", 0) + start_time
                    seg["end"] = seg.get("end", 0) + start_time
                    all_segments.append(seg)
            finally:
                try:
                    os.unlink(chunk_path)
                except OSError:
                    pass

        return {
            "segments": all_segments,
            "language": detected_language or "en",
            "text": " ".join(s.get("text", "") for s in all_segments),
        }

    # ------------------------------------------------------------------
    # Конвертация формата
    # ------------------------------------------------------------------
    def _convert_segments(self, raw: Dict) -> List[Dict]:
        """
        Конвертирует формат ответа OpenAI в наш внутренний формат сегментов.

        OpenAI возвращает:
            {"segments": [{"start", "end", "text", "speaker", ...}], ...}

        Наш формат:
            [{"start", "end", "text", "speaker"}, ...]
        """
        segments = []
        for seg in raw.get("segments", []):
            entry = {
                "start": float(seg.get("start", 0)),
                "end": float(seg.get("end", 0)),
                "text": seg.get("text", "").strip(),
            }
            # diarized_json возвращает speaker как "A", "B", ... или имя
            # Конвертируем в наш формат SPEAKER_00, SPEAKER_01
            speaker = seg.get("speaker", "")
            if speaker:
                # "A" → 0, "B" → 1, ...  или "speaker_0" → 0
                if len(speaker) == 1 and speaker.isalpha():
                    num = ord(speaker.upper()) - ord("A")
                    entry["speaker"] = f"SPEAKER_{num:02d}"
                elif "_" in speaker:
                    try:
                        num = int(speaker.split("_")[-1])
                        entry["speaker"] = f"SPEAKER_{num:02d}"
                    except (ValueError, IndexError):
                        entry["speaker"] = speaker.upper().replace(" ", "_")
                else:
                    entry["speaker"] = speaker.upper().replace(" ", "_")
            else:
                entry["speaker"] = "SPEAKER_00"

            if entry["text"]:
                segments.append(entry)

        return segments

"""
Voicer API TTS — озвучка через https://voiceapi.csv666.ru

Использует облачный TTS (ElevenLabs через прокси-API).
Асинхронный flow: создание задачи → polling статуса → скачивание результата.

Поддерживает голосовые пресеты с привязкой к спикерам по языку и приоритету.
"""

import os
import time
import requests
import tempfile
from pathlib import Path
from typing import List, Dict, Optional, Callable

from core.config import APP_PATHS

VOICER_BASE_URL = "https://voiceapi.csv666.ru"

# Интервал опроса статуса задачи (секунды)
POLL_INTERVAL = 1.0
# Максимальное время ожидания одной задачи (секунды)
MAX_WAIT_TIME = 120

# Дефолтные настройки голоса (ElevenLabs multilingual v2)
DEFAULT_VOICE_SETTINGS = {
    "model_id": "eleven_multilingual_v2",
    "voice_settings": {
        "stability": 0.85,
        "similarity_boost": 0.75,
        "use_speaker_boost": True,
        "speed": 1.0,
    }
}


class VoicerTTS:
    """
    Генерация озвучки через Voicer API (voiceapi.csv666.ru).

    Работает с голосовыми пресетами (template_uuid) — каждый спикер
    получает свой голос на основе языка и позиции пресета (приоритет).
    """

    def __init__(
        self,
        api_key: str,
        presets: Optional[List[Dict]] = None,
        progress_callback: Optional[Callable[[str], None]] = None,
        should_stop_callback: Optional[Callable[[], bool]] = None,
    ):
        self.api_key = api_key
        self.presets = presets or []
        self.progress_callback = progress_callback
        self.should_stop_callback = should_stop_callback
        self.temp_tts_dir = APP_PATHS['temp'] / "tts_parts"
        self.temp_tts_dir.mkdir(parents=True, exist_ok=True)
        # Кэш: speaker -> template_uuid
        self._speaker_voice_map: Dict[str, str] = {}

    def _log(self, msg: str):
        if self.progress_callback:
            self.progress_callback(msg)

    def _headers(self) -> dict:
        return {"X-API-Key": self.api_key}

    # ── API helpers ─────────────────────────────────────────────────────────

    def validate_key(self) -> bool:
        """Проверяет валидность API ключа через GET /balance."""
        try:
            resp = requests.get(
                f"{VOICER_BASE_URL}/balance",
                headers=self._headers(),
                timeout=10,
            )
            return resp.status_code == 200
        except Exception:
            return False

    def get_balance(self) -> Optional[dict]:
        """Возвращает баланс аккаунта."""
        try:
            resp = requests.get(
                f"{VOICER_BASE_URL}/balance",
                headers=self._headers(),
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json()
            return None
        except Exception:
            return None

    def get_templates(self) -> List[dict]:
        """Получает список сохранённых голосовых шаблонов."""
        try:
            resp = requests.get(
                f"{VOICER_BASE_URL}/templates",
                headers=self._headers(),
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json()
            return []
        except Exception:
            return []

    # ── Preset → Speaker assignment ──────────────────────────────────────

    def _get_presets_for_language(self, target_lang: str) -> List[Dict]:
        """
        Фильтрует пресеты по целевому языку.
        Возвращает пресеты отсортированные по позиции (порядок = приоритет).
        Если нет пресетов для языка — возвращает все пресеты.
        """
        lang_upper = target_lang.upper()

        # Маппинг коротких кодов в полные названия
        lang_code_map = {
            'RU': 'RUSSIAN', 'EN': 'ENGLISH', 'ES': 'SPANISH',
            'FR': 'FRENCH', 'DE': 'GERMAN', 'IT': 'ITALIAN',
            'PT': 'PORTUGUESE', 'PL': 'POLISH', 'TR': 'TURKISH',
            'NL': 'DUTCH', 'CS': 'CZECH', 'AR': 'ARABIC',
            'ZH-CN': 'CHINESE', 'HU': 'HUNGARIAN', 'KO': 'KOREAN',
            'JA': 'JAPANESE', 'HI': 'HINDI',
        }
        lang_name = lang_code_map.get(lang_upper, lang_upper)

        # Фильтруем пресеты по языку
        matched = [
            p for p in self.presets
            if lang_name in [l.upper() for l in p.get('languages', [])]
        ]

        if matched:
            # Сортируем по langPriority для этого языка (per-language hierarchy из UI)
            matched.sort(key=lambda p: p.get('langPriority', {}).get(lang_name, 999))
            return matched

        # Если нет пресетов для этого языка — возвращаем все
        if self.presets:
            self._log(f"   No presets for {lang_name}, using all {len(self.presets)} presets")
            return self.presets

        return []

    def _assign_voice_to_speaker(self, speaker: str, target_lang: str) -> Optional[str]:
        """
        Назначает голосовой пресет спикеру.
        Использует round-robin по пресетам, отфильтрованным по языку.
        Кэширует назначение — один спикер всегда получает один и тот же голос.
        """
        if speaker in self._speaker_voice_map:
            return self._speaker_voice_map[speaker]

        available = self._get_presets_for_language(target_lang)
        if not available:
            return None

        # Round-robin: назначаем пресет по индексу уже назначенных спикеров
        idx = len(self._speaker_voice_map) % len(available)
        preset = available[idx]
        template_id = preset.get('templateId', '')

        if template_id:
            self._speaker_voice_map[speaker] = template_id
            self._log(f"   {speaker} -> voice \"{preset.get('name', 'unnamed')}\" (#{idx + 1})")
            return template_id

        return None

    def _create_task(self, text: str, template_uuid: Optional[str] = None) -> Optional[str]:
        """Создаёт задачу синтеза речи. Возвращает task_id."""
        payload = {"text": text}
        if template_uuid:
            payload["template_uuid"] = template_uuid
        else:
            self._log("   No voice template specified for this segment!")
            return None

        resp = requests.post(
            f"{VOICER_BASE_URL}/tasks",
            headers=self._headers(),
            json=payload,
            timeout=30,
        )

        if resp.status_code == 200:
            data = resp.json()
            return data.get("task_id")
        else:
            error_detail = ""
            try:
                error_detail = resp.json().get("detail", resp.text)
            except Exception:
                error_detail = resp.text
            self._log(f"   Task creation failed ({resp.status_code}): {error_detail}")
            return None

    def _wait_for_task(self, task_id: str) -> bool:
        """Ждёт завершения задачи. Возвращает True если задача готова."""
        start = time.time()
        while time.time() - start < MAX_WAIT_TIME:
            if self.should_stop_callback and self.should_stop_callback():
                raise InterruptedError("Processing stopped by user")

            try:
                resp = requests.get(
                    f"{VOICER_BASE_URL}/tasks/{task_id}/status",
                    headers=self._headers(),
                    timeout=10,
                )
                if resp.status_code == 200:
                    status = resp.json().get("status", "")
                    if status == "ending":
                        return True
                    elif status in ("error", "error_handled"):
                        self._log(f"   Task {task_id} failed with status: {status}")
                        return False
                    # waiting, processing, ending_processed — продолжаем ждать
            except Exception as e:
                self._log(f"   Poll error: {e}")

            time.sleep(POLL_INTERVAL)

        self._log(f"   Task {task_id} timed out after {MAX_WAIT_TIME}s")
        return False

    def _download_result(self, task_id: str, output_path: str) -> bool:
        """Скачивает результат задачи (MP3) и сохраняет."""
        try:
            resp = requests.get(
                f"{VOICER_BASE_URL}/tasks/{task_id}/result",
                headers=self._headers(),
                timeout=60,
            )
            if resp.status_code == 200:
                # Сохраняем MP3
                mp3_path = output_path.replace(".wav", ".mp3")
                with open(mp3_path, "wb") as f:
                    f.write(resp.content)

                # Конвертируем MP3 → WAV (24kHz mono) через ffmpeg
                self._convert_to_wav(mp3_path, output_path)

                # Удаляем MP3
                try:
                    os.remove(mp3_path)
                except Exception:
                    pass

                return os.path.exists(output_path)
            else:
                self._log(f"   Download failed ({resp.status_code})")
                return False
        except Exception as e:
            self._log(f"   Download error: {e}")
            return False

    def _convert_to_wav(self, mp3_path: str, wav_path: str):
        """Конвертирует MP3 в WAV 24kHz mono через ffmpeg."""
        import subprocess
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-i", mp3_path,
                    "-ar", "24000", "-ac", "1",
                    "-sample_fmt", "s16",
                    wav_path,
                ],
                capture_output=True,
                timeout=30,
            )
        except FileNotFoundError:
            # ffmpeg не найден — пробуем через pydub
            try:
                from pydub import AudioSegment
                audio = AudioSegment.from_mp3(mp3_path)
                audio = audio.set_frame_rate(24000).set_channels(1).set_sample_width(2)
                audio.export(wav_path, format="wav")
            except Exception as e:
                self._log(f"   WAV conversion failed: {e}")
                # Крайний случай — просто переименовываем MP3 в WAV
                import shutil
                shutil.copy2(mp3_path, wav_path)

    def _get_audio_duration(self, wav_path: str) -> float:
        """Возвращает длительность WAV файла в секундах."""
        try:
            import soundfile as sf
            info = sf.info(wav_path)
            return info.duration
        except Exception:
            try:
                import wave
                with wave.open(wav_path, "r") as w:
                    return w.getnframes() / w.getframerate()
            except Exception:
                return 0.0

    # ── Main generation ─────────────────────────────────────────────────────

    def generate_dubbing(
        self,
        segments: List[Dict],
        target_lang: str = "ru",
    ) -> List[Dict]:
        """
        Генерирует озвучку для всех сегментов через Voicer API.

        Назначает голосовые пресеты спикерам:
        - Фильтрует пресеты по целевому языку
        - Round-robin распределение по спикерам (позиция = приоритет)
        - Один спикер всегда получает один и тот же голос
        """
        if not segments:
            self._log("No segments for dubbing")
            return segments

        # Проверяем баланс
        balance = self.get_balance()
        if balance:
            self._log(f"Voicer API balance: {balance.get('balance_text', 'unknown')}")

        # Проверяем наличие пресетов
        if not self.presets:
            self._log("WARNING: No voice presets configured!")
            self._log("   Add presets in the DUBBING section -> VOICE PRESETS")
            # Пробуем использовать первый шаблон из аккаунта как fallback
            templates = self.get_templates()
            if templates:
                fallback_uuid = templates[0].get("uuid")
                fallback_name = templates[0].get("name", "unknown")
                self._log(f"   Fallback: using account template \"{fallback_name}\"")
                self.presets = [{
                    'id': 'fallback',
                    'name': fallback_name,
                    'templateId': fallback_uuid,
                    'languages': [],
                }]
            else:
                self._log("ERROR: No voice presets and no templates in Voicer account!")
                return segments

        # Логируем назначение голосов
        available = self._get_presets_for_language(target_lang)
        self._log(f"Voice presets for {target_lang.upper()}: {len(available)} available")
        for i, p in enumerate(available):
            self._log(f"   #{i + 1}: {p.get('name', 'unnamed')} ({p.get('templateId', 'no-id')[:8]}...)")

        # Собираем уникальных спикеров и назначаем голоса
        speakers = list(dict.fromkeys(
            seg.get("speaker", "SPEAKER_UNKNOWN") for seg in segments if seg.get("text", "").strip()
        ))
        self._log(f"Speakers detected: {len(speakers)} — {', '.join(speakers)}")

        for spk in speakers:
            self._assign_voice_to_speaker(spk, target_lang)

        total_segments = len(segments)
        self._log(f"Generating dubbing for {total_segments} segments (Voicer API)...")

        # Elastic timing
        from core.elastic_timing import compute_effective_durations
        compute_effective_durations(segments)

        updated_segments = []
        success_count = 0
        error_count = 0
        start_time = time.time()

        for i, seg in enumerate(segments):
            if self.should_stop_callback and self.should_stop_callback():
                self._log("Dubbing generation stopped by user")
                raise InterruptedError("Processing stopped by user")

            text = seg.get("text", "").strip()
            speaker = seg.get("speaker", "SPEAKER_UNKNOWN")
            original_duration = float(seg.get("end", 0)) - float(seg.get("start", 0))
            effective_duration = float(seg.get("effective_duration", original_duration))

            if not text:
                updated_segments.append(seg)
                continue

            output_path = str(self.temp_tts_dir / f"segment_{i:04d}.wav")

            progress = ((i + 1) / total_segments) * 100
            elapsed = time.time() - start_time
            avg_time = elapsed / (i + 1) if i > 0 else 0
            remaining = avg_time * (total_segments - (i + 1))

            # Получаем template для этого спикера
            template_uuid = self._speaker_voice_map.get(speaker)

            self._log(
                f"[{i+1}/{total_segments}] ({progress:.0f}%) {speaker} | "
                f"{len(text)} chars | budget: {effective_duration:.1f}s | "
                f"~{remaining/60:.1f} min left"
            )

            try:
                # Создаём задачу с конкретным template
                task_id = self._create_task(text, template_uuid=template_uuid)
                if not task_id:
                    raise RuntimeError("Failed to create TTS task")

                # Ждём выполнения
                if not self._wait_for_task(task_id):
                    raise RuntimeError(f"Task {task_id} did not complete")

                # Скачиваем результат
                if not self._download_result(task_id, output_path):
                    raise RuntimeError(f"Failed to download task {task_id} result")

                actual_duration = self._get_audio_duration(output_path)

                seg_copy = seg.copy()
                seg_copy["audio_file"] = output_path
                seg_copy["actual_audio_duration"] = actual_duration
                updated_segments.append(seg_copy)
                success_count += 1

                self._log(
                    f"   {actual_duration:.1f}s / {effective_duration:.1f}s "
                    f"({actual_duration/effective_duration:.0%})"
                )

            except InterruptedError:
                raise
            except Exception as e:
                self._log(f"[{i+1}/{total_segments}] Error: {e}")
                updated_segments.append(seg)
                error_count += 1
                continue

        total_time = time.time() - start_time
        self._log(
            f"Dubbing complete: {success_count}/{total_segments} OK, "
            f"{error_count} errors | Total: {total_time/60:.1f} min"
        )

        return updated_segments

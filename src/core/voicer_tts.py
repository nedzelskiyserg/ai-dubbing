"""
Voicer API TTS — озвучка через https://voiceapi.csv666.ru

Использует облачный TTS (ElevenLabs через прокси-API).
Асинхронный flow: создание задачи → polling статуса → скачивание результата.

Поддерживает голосовые пресеты с привязкой к спикерам по языку и приоритету.
"""

import os
import time
import threading
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from concurrent.futures import ThreadPoolExecutor, as_completed
import tempfile
from pathlib import Path
from typing import List, Dict, Optional, Callable
from core.text_normalizer import normalize_for_tts

from core.config import APP_PATHS

VOICER_BASE_URL = "https://voiceapi.csv666.ru"


def _create_session() -> requests.Session:
    """Создаёт requests Session с retry-логикой и экспоненциальным backoff."""
    session = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=1.0,       # 1s, 2s, 4s, 8s, 16s
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

# Интервал опроса статуса задачи (секунды)
POLL_INTERVAL = 1.0
# Максимальное время ожидания одной задачи (секунды)
MAX_WAIT_TIME = 300
# Максимум параллельных запросов к API
MAX_PARALLEL = 5
# Retry при 429 (лимит активных задач)
RATE_LIMIT_RETRY_DELAY = 10  # секунд между retry
RATE_LIMIT_MAX_RETRIES = 30  # макс retry (~5 минут)

# Минимальная длина текста в одном запросе к Voicer API (ограничение сервиса).
# Тексты короче батчатся по спикерам или паддятся фиктивным текстом.
MIN_TEXT_LENGTH = 500
# Целевой верхний размер бакета при батчинге (чтобы не отправлять слишком длинные пачки).
BATCH_TARGET_LENGTH = 1400
# Разделитель между фрагментами в бакете — даёт стабильную паузу для silence-split.
# Точки произносятся медленно + переносы строк = ~600–800 мс тишины в выходном аудио.
BATCH_SEPARATOR = "\n\n. . . . .\n\n"
# Параметры silence-split (pydub.silence.split_on_silence)
SPLIT_MIN_SILENCE_MS = 350
SPLIT_SILENCE_THRESH_DB = -40
SPLIT_KEEP_SILENCE_MS = 80

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
        # HTTP session с retry-логикой
        self._session = _create_session()
        # Lock для потокобезопасного логирования и счётчиков
        self._log_lock = threading.Lock()
        self._progress_lock = threading.Lock()

    def _log(self, msg: str):
        if self.progress_callback:
            with self._log_lock:
                self.progress_callback(msg)

    def _headers(self) -> dict:
        return {"X-API-Key": self.api_key}

    # ── API helpers ─────────────────────────────────────────────────────────

    def validate_key(self) -> bool:
        """Проверяет валидность API ключа через GET /balance."""
        try:
            resp = self._session.get(
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
            resp = self._session.get(
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
            resp = self._session.get(
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
        """Создаёт задачу синтеза речи. Возвращает task_id. Retry при 429."""
        payload = {"text": text}
        if template_uuid:
            payload["template_uuid"] = template_uuid
        else:
            self._log("   No voice template specified for this segment!")
            return None

        for attempt in range(RATE_LIMIT_MAX_RETRIES + 1):
            if self.should_stop_callback and self.should_stop_callback():
                raise InterruptedError("Processing stopped by user")

            resp = self._session.post(
                f"{VOICER_BASE_URL}/tasks",
                headers=self._headers(),
                json=payload,
                timeout=30,
            )

            if resp.status_code == 200:
                data = resp.json()
                return data.get("task_id")
            elif resp.status_code == 429:
                # Лимит активных задач — ждём и пробуем снова
                if attempt < RATE_LIMIT_MAX_RETRIES:
                    time.sleep(RATE_LIMIT_RETRY_DELAY)
                    continue
                else:
                    self._log(f"   Task creation failed: rate limit after {attempt + 1} retries")
                    return None
            else:
                error_detail = ""
                try:
                    error_detail = resp.json().get("detail", resp.text)
                except Exception:
                    error_detail = resp.text
                self._log(f"   Task creation failed ({resp.status_code}): {error_detail}")
                return None

        return None

    def _wait_for_task(self, task_id: str) -> bool:
        """Ждёт завершения задачи. Возвращает True если задача готова."""
        start = time.time()
        while time.time() - start < MAX_WAIT_TIME:
            if self.should_stop_callback and self.should_stop_callback():
                raise InterruptedError("Processing stopped by user")

            try:
                resp = self._session.get(
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
            resp = self._session.get(
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

    # ── Batching helpers (для обхода ограничения API на min 500 chars) ─────

    @staticmethod
    def _pad_text(text: str) -> str:
        """
        Добивает короткий текст нейтральным филлером до MIN_TEXT_LENGTH.
        Филлер ставится ПОСЛЕ реального текста за явной паузой, чтобы:
          (а) API приняло запрос (≥500 chars),
          (б) реальная речь была в начале аудио,
          (в) video_maker при smart_trim по effective_duration отрезал филлер.
        """
        if len(text) >= MIN_TEXT_LENGTH:
            return text
        tail = ". . . . . . . . . . "
        padded = text.rstrip() + BATCH_SEPARATOR + tail
        while len(padded) < MIN_TEXT_LENGTH + 10:
            padded += tail
        return padded

    def _build_units(self, normalized_jobs: List[tuple]) -> List[Dict]:
        """
        Группирует нормализованные jobs в «юниты» для отправки в API.

        Юнит — одна единица работы для одного API-запроса. Типы:
          - "single": один job, текст уже ≥500 chars, отправляется как есть.
          - "padded": один job <500 chars, отправляется с паддингом (fallback-режим,
             когда не с кем батчить — например одинокий короткий сегмент спикера).
          - "bucket": 2+ jobs одного спикера, склеенные через BATCH_SEPARATOR,
             суммарно ≥MIN_TEXT_LENGTH. Результат режется по тишине.

        Бакеты строятся per-speaker (не обязательно смежно по времени), чтобы
        покрыть диалоги A-B-A-B, где смежный батчинг бесполезен.
        """
        long_jobs = []
        short_by_speaker: Dict[str, List[tuple]] = {}
        for job in normalized_jobs:
            _, _, _, _, _, speaker, norm_text = job
            if len(norm_text) >= MIN_TEXT_LENGTH:
                long_jobs.append(job)
            else:
                short_by_speaker.setdefault(speaker, []).append(job)

        units: List[Dict] = []

        for job in long_jobs:
            units.append({"type": "single", "jobs": [job], "template_uuid": job[3]})

        for speaker, shorts in short_by_speaker.items():
            # Сохраняем хронологический порядок внутри спикера — важно для интонации.
            shorts.sort(key=lambda j: j[0])
            template_uuid = shorts[0][3]
            bucket: List[tuple] = []
            bucket_len = 0
            for j in shorts:
                t_len = len(j[6]) + len(BATCH_SEPARATOR)
                # Не раздуваем бакет бесконечно — закрываем по верхней границе.
                if bucket and bucket_len + t_len > BATCH_TARGET_LENGTH and bucket_len >= MIN_TEXT_LENGTH:
                    units.append({"type": "bucket", "jobs": bucket, "template_uuid": template_uuid})
                    bucket = []
                    bucket_len = 0
                bucket.append(j)
                bucket_len += t_len

            if bucket:
                if bucket_len >= MIN_TEXT_LENGTH and len(bucket) >= 2:
                    units.append({"type": "bucket", "jobs": bucket, "template_uuid": template_uuid})
                elif len(bucket) >= 2:
                    # Бакет есть, но суммарно <500 — попробуем склеить с предыдущим бакетом того же спикера.
                    prev_bucket = next(
                        (u for u in reversed(units)
                         if u["type"] == "bucket" and u["jobs"][0][5] == speaker),
                        None,
                    )
                    if prev_bucket is not None:
                        prev_bucket["jobs"].extend(bucket)
                    else:
                        # Некуда мержить — отправляем поштучно с паддингом.
                        for j in bucket:
                            units.append({"type": "padded", "jobs": [j], "template_uuid": j[3]})
                else:
                    # Одинокий короткий сегмент — только паддинг.
                    units.append({"type": "padded", "jobs": bucket, "template_uuid": bucket[0][3]})

        return units

    def _split_audio_by_silence(
        self,
        source_wav: str,
        output_paths: List[str],
    ) -> bool:
        """
        Режет WAV-файл на len(output_paths) кусков по тишине.
        Пытается несколько наборов параметров — аудио от TTS не всегда даёт
        одинаковые паузы. Возвращает True, если получилось ровное количество кусков.
        """
        try:
            from pydub import AudioSegment
            from pydub.silence import split_on_silence
        except ImportError:
            self._log("   pydub not available — cannot split bucket audio")
            return False

        try:
            audio = AudioSegment.from_file(source_wav)
        except Exception as e:
            self._log(f"   Failed to load bucket audio: {e}")
            return False

        expected = len(output_paths)
        # Пробуем разные пороги, начиная с «жёсткого» к «мягкому».
        # Жёсткий = более длинные паузы требуются, меньше ложных срабатываний.
        param_sets = [
            (SPLIT_MIN_SILENCE_MS, SPLIT_SILENCE_THRESH_DB),
            (450, -38),
            (300, -42),
            (250, -36),
            (200, -45),
        ]

        chunks = None
        for min_silence, thresh in param_sets:
            try:
                attempt = split_on_silence(
                    audio,
                    min_silence_len=min_silence,
                    silence_thresh=thresh,
                    keep_silence=SPLIT_KEEP_SILENCE_MS,
                )
            except Exception:
                continue
            if len(attempt) == expected:
                chunks = attempt
                break

        if chunks is None:
            self._log(
                f"   Silence split mismatch: expected {expected} pieces, "
                f"got varying counts across thresholds"
            )
            return False

        for chunk, out_path in zip(chunks, output_paths):
            try:
                chunk.set_frame_rate(24000).set_channels(1).set_sample_width(2).export(
                    out_path, format="wav"
                )
            except Exception as e:
                self._log(f"   Failed to export split chunk: {e}")
                return False

        return True

    def _run_unit(self, unit: Dict, target_lang: str) -> List[tuple]:
        """
        Обрабатывает один юнит (single / padded / bucket):
          - формирует текст,
          - создаёт одну задачу в API,
          - ждёт результат,
          - скачивает в tmp,
          - (для bucket) режет по тишине на куски → раскладывает по output_path.
        Возвращает список результатов: [(idx, seg_copy, actual_dur, eff_dur), ...]
        Бросает исключение при фатальной ошибке юнита.
        """
        jobs = unit["jobs"]
        template_uuid = unit["template_uuid"]
        unit_type = unit["type"]

        if unit_type == "single":
            job = jobs[0]
            idx, seg, output_path, _, eff_dur, speaker, norm_text = job
            api_text = norm_text
        elif unit_type == "padded":
            job = jobs[0]
            idx, seg, output_path, _, eff_dur, speaker, norm_text = job
            api_text = self._pad_text(norm_text)
        elif unit_type == "bucket":
            api_text = BATCH_SEPARATOR.join(j[6] for j in jobs)
            if len(api_text) < MIN_TEXT_LENGTH:
                api_text = self._pad_text(api_text)
        else:
            raise ValueError(f"Unknown unit type: {unit_type}")

        if unit_type == "bucket":
            speakers_in_bucket = {j[5] for j in jobs}
            label = f"BUCKET[{len(jobs)} segs, {', '.join(sorted(speakers_in_bucket))}]"
        else:
            label = f"[{jobs[0][0]+1}] {jobs[0][5]}"
        self._log(f"   ▶ {label} | {len(api_text)} chars")

        task_id = self._create_task(api_text, template_uuid=template_uuid)
        if not task_id:
            raise RuntimeError(f"Failed to create TTS task for {label}")
        if not self._wait_for_task(task_id):
            raise RuntimeError(f"Task {task_id} did not complete ({label})")

        # Для bucket скачиваем во временный файл, потом режем.
        if unit_type == "bucket":
            tmp_wav = str(self.temp_tts_dir / f"_bucket_{task_id}.wav")
            if not self._download_result(task_id, tmp_wav):
                raise RuntimeError(f"Failed to download bucket task {task_id}")

            output_paths = [j[2] for j in jobs]
            ok = self._split_audio_by_silence(tmp_wav, output_paths)
            try:
                os.remove(tmp_wav)
            except Exception:
                pass

            if not ok:
                # Fallback: перезапускаем каждый сегмент бакета как padded single.
                self._log(f"   Bucket split failed — falling back to padded per-segment")
                results = []
                for j in jobs:
                    fb_unit = {"type": "padded", "jobs": [j], "template_uuid": template_uuid}
                    results.extend(self._run_unit(fb_unit, target_lang))
                return results

            results = []
            for j in jobs:
                idx, seg, out_path, _, eff_dur, speaker, _ = j
                actual_dur = self._get_audio_duration(out_path)
                seg_copy = seg.copy()
                seg_copy["audio_file"] = out_path
                seg_copy["actual_audio_duration"] = actual_dur
                results.append((idx, seg_copy, actual_dur, eff_dur))
            return results

        # single / padded: скачиваем сразу в итоговый путь.
        if not self._download_result(task_id, output_path):
            raise RuntimeError(f"Failed to download task {task_id} ({label})")

        actual_dur = self._get_audio_duration(output_path)
        seg_copy = seg.copy()
        seg_copy["audio_file"] = output_path
        seg_copy["actual_audio_duration"] = actual_dur
        return [(idx, seg_copy, actual_dur, eff_dur)]

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
        self._log(f"Generating dubbing for {total_segments} segments (Voicer API, {MAX_PARALLEL}x parallel)...")

        # Elastic timing
        from core.elastic_timing import compute_effective_durations
        compute_effective_durations(segments)

        # Подготовка нормализованных jobs: собираем сегменты с текстом,
        # нормализация происходит СЕЙЧАС (а не в воркере), чтобы корректно
        # считать длину текста при батчинге.
        normalized_jobs = []
        # Формат: (index, seg, output_path, template_uuid, effective_duration, speaker, normalized_text)
        for i, seg in enumerate(segments):
            text = seg.get("text", "").strip()
            if not text:
                continue
            norm_text = normalize_for_tts(text, target_lang)
            if not norm_text.strip():
                continue
            speaker = seg.get("speaker", "SPEAKER_UNKNOWN")
            original_duration = float(seg.get("end", 0)) - float(seg.get("start", 0))
            effective_duration = float(seg.get("effective_duration", original_duration))
            template_uuid = self._speaker_voice_map.get(speaker)
            output_path = str(self.temp_tts_dir / f"segment_{i:04d}.wav")
            normalized_jobs.append(
                (i, seg, output_path, template_uuid, effective_duration, speaker, norm_text)
            )

        total_jobs = len(normalized_jobs)
        self._log(f"   {total_jobs} segments to generate, {total_segments - total_jobs} empty/skipped")

        # Группируем в юниты для батчинга (обход ограничения API на MIN_TEXT_LENGTH).
        units = self._build_units(normalized_jobs)
        n_single = sum(1 for u in units if u["type"] == "single")
        n_padded = sum(1 for u in units if u["type"] == "padded")
        n_bucket = sum(1 for u in units if u["type"] == "bucket")
        bucket_coverage = sum(len(u["jobs"]) for u in units if u["type"] == "bucket")
        self._log(
            f"   Batching: {len(units)} API calls → {n_single} single, "
            f"{n_padded} padded, {n_bucket} bucket (covers {bucket_coverage} segs)"
        )

        # Результаты: index → обновлённый сегмент
        results: Dict[int, Dict] = {}
        success_count = 0
        error_count = 0
        completed_count = 0
        start_time = time.time()

        # Семафор контролирует кол-во АКТИВНЫХ задач на сервере (не потоков!)
        # Освобождается только когда задача ЗАВЕРШЕНА на сервере (success/error)
        api_slots = threading.Semaphore(MAX_PARALLEL)

        def _process_unit(unit):
            """Воркер: захватить слот → обработать юнит (может вернуть >1 результата) → освободить слот."""
            if self.should_stop_callback and self.should_stop_callback():
                raise InterruptedError("Processing stopped by user")
            api_slots.acquire()
            try:
                return self._run_unit(unit, target_lang)
            finally:
                api_slots.release()

        interrupted = False
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL + 2) as executor:
            future_to_unit = {
                executor.submit(_process_unit, u): u for u in units
            }

            try:
                for future in as_completed(future_to_unit):
                    if self.should_stop_callback and self.should_stop_callback():
                        interrupted = True
                        for f in future_to_unit:
                            f.cancel()
                        break

                    unit = future_to_unit[future]
                    n_in_unit = len(unit["jobs"])

                    try:
                        unit_results = future.result()
                        for idx, seg_copy, actual_dur, eff_dur in unit_results:
                            results[idx] = seg_copy
                            with self._progress_lock:
                                success_count += 1
                                completed_count += 1
                            ratio = (actual_dur / eff_dur) if eff_dur > 0 else 0.0
                            self._log(
                                f"   ✓ [{idx+1}/{total_segments}] "
                                f"{actual_dur:.1f}s / {eff_dur:.1f}s ({ratio:.0%}) | "
                                f"{completed_count}/{total_jobs} done"
                            )
                    except InterruptedError:
                        interrupted = True
                        for f in future_to_unit:
                            f.cancel()
                        break
                    except Exception as e:
                        with self._progress_lock:
                            error_count += n_in_unit
                            completed_count += n_in_unit
                        failed_indices = ", ".join(str(j[0] + 1) for j in unit["jobs"])
                        self._log(f"   ✗ [{failed_indices}] Unit error: {e}")
            except InterruptedError:
                interrupted = True

        if interrupted:
            self._log("Dubbing generation stopped by user")
            raise InterruptedError("Processing stopped by user")

        # Собираем финальный список с сохранением порядка
        updated_segments = []
        for i, seg in enumerate(segments):
            if i in results:
                updated_segments.append(results[i])
            else:
                updated_segments.append(seg)

        total_time = time.time() - start_time
        self._log(
            f"Dubbing complete: {success_count}/{total_jobs} OK, "
            f"{error_count} errors | Total: {total_time/60:.1f} min"
        )

        return updated_segments

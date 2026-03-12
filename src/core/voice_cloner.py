# -*- coding: utf-8 -*-
"""
Voice Cloning Module using F5-TTS — Intelligent Dubbing Engine

Генерация дубляжа с клонированием голоса спикеров.
Интеллектуальная система синхронизации:
  - Timing-aware TTS: знает бюджет времени для каждого сегмента
  - Adaptive retry: если аудио слишком длинное, сокращает текст и перегенерирует
  - Elastic timing: расширяется в паузы между фразами как профессиональный дублёр
  - Фактические длительности сохраняются для адаптивной сборки в VideoMaker

F5-TTS: Diffusion Transformer + ConvNeXt V2, Flow Matching
- Английский: F5TTS_v1_Base (SWivid/F5-TTS, auto-download ~3GB)
- Русский: Misha24-10/F5-TTS_RUSSIAN v2 (community model)
- RUAccent: русские ударения для естественного произношения
"""
import os
import re
import sys
import time
import struct
from pathlib import Path
from typing import List, Dict, Optional, Callable, Tuple

import numpy as np
import soundfile as sf
from pydub import AudioSegment
from core.config import APP_PATHS

# ── Constants ──────────────────────────────────────────────────────────────

# F5-TTS quality settings (confirmed optimal)
F5_NFE_STEP = 48          # default 32, higher = cleaner but slower
F5_CFG_STRENGTH = 2.0     # classifier-free guidance
F5_SWAY_COEF = -1.0       # sway sampling coefficient
F5_SEED = 123             # best prosody seed (confirmed by testing)

# F5-TTS sample rate
F5_SAMPLE_RATE = 24000

# Reference audio constraints
MAX_REF_DURATION_SEC = 12.0   # F5-TTS supports up to 12s reference
MIN_TEXT_CHARS = 10

# Russian model (Misha24-10/F5-TTS_RUSSIAN)
RU_MODEL_REPO = "Misha24-10/F5-TTS_RUSSIAN"
RU_VOCAB_PATH = "F5TTS_v1_Base/vocab.txt"
RU_CKPT_PATH = "F5TTS_v1_Base_v2/model_last_inference.safetensors"

# Languages that use the Russian model
RU_LANGS = {"ru"}

# ── Timing-aware TTS constants ────────────────────────────────────────────

# Maximum atempo speedup that VideoMaker can apply without quality loss
MAX_ATEMPO_TOLERANCE = 1.10

# Timing retry: max retries when TTS audio exceeds time budget
MAX_TIMING_RETRIES = 2

# Timing tolerance: how much over budget is acceptable (VideoMaker handles via atempo)
# 1.08 means we accept audio up to 8% longer than budget (VideoMaker uses gentle atempo)
TIMING_TOLERANCE_RATIO = 1.08


class VoiceCloner:
    """
    Клонирование голоса и генерация дубляжа через F5-TTS.

    Интеллектуальная система синхронизации:
    1. Вычисляет бюджет времени для каждого сегмента (elastic timing)
    2. Генерирует TTS и проверяет фактическую длительность
    3. Если превышение > 8%: сокращает текст и перегенерирует (до 2 раз)
    4. Сохраняет фактические длительности для адаптивной сборки VideoMaker
    """

    def __init__(
        self,
        progress_callback: Optional[Callable[[str], None]] = None,
        should_stop_callback: Optional[Callable[[], bool]] = None
    ):
        self.progress_callback = progress_callback
        self.should_stop_callback = should_stop_callback

        # F5-TTS models (lazy loaded)
        self._tts_en = None
        self._tts_ru = None

        # RUAccent instance (lazy loaded, reused)
        self._accentizer = None

        # Speaker reference texts (extracted from transcription segments)
        self._speaker_ref_texts: Dict[str, str] = {}

        # Определяем устройство
        self.device = self._detect_device()

        # Создаем необходимые директории
        self.voices_dir = APP_PATHS['base'] / "voices"
        try:
            self.voices_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self._log(f"Warning: cannot create voices dir: {e}")

        self.temp_tts_dir = APP_PATHS['temp'] / "tts_parts"
        try:
            self.temp_tts_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self._log(f"Warning: cannot create temp/tts_parts dir: {e}")

        self._log(f"VoiceCloner initialized (F5-TTS, device: {self.device})")

    def _log(self, msg: str):
        """Логирование в UI и консоль"""
        print(msg)
        if self.progress_callback:
            self.progress_callback(msg)

    def _detect_device(self) -> str:
        """Определяет устройство: CUDA > MPS > CPU."""
        import torch

        if torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(0)
            props = torch.cuda.get_device_properties(0)
            vram_gb = (getattr(props, 'total_memory', 0) or getattr(props, 'total_mem', 0)) / (1024**3)
            self._log(f"NVIDIA GPU: {gpu_name} ({vram_gb:.1f} GB VRAM, CUDA {torch.version.cuda})")
            return "cuda"

        if hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
            self._log("Apple Silicon GPU (MPS)")
            return "mps"

        cuda_built = getattr(torch.version, 'cuda', None)
        if cuda_built:
            self._log(f"PyTorch built with CUDA {cuda_built}, but no GPU detected. Using CPU.")
        elif sys.platform == 'win32':
            self._log("PyTorch installed without CUDA. TTS will run on CPU (slower).")

        return "cpu"

    # ── F5-TTS Engine ──────────────────────────────────────────────────────

    def _load_model(self, lang: str = "en"):
        """Ленивая загрузка F5-TTS модели."""
        is_russian = lang in RU_LANGS

        if is_russian and self._tts_ru is not None:
            return
        if not is_russian and self._tts_en is not None:
            return

        from f5_tts.api import F5TTS

        if is_russian:
            self._log("Loading F5-TTS Russian model (Misha24-10 v2)...")
            load_start = time.time()

            from huggingface_hub import hf_hub_download
            vocab_path = hf_hub_download(RU_MODEL_REPO, RU_VOCAB_PATH)
            ckpt_path = hf_hub_download(RU_MODEL_REPO, RU_CKPT_PATH)

            self._tts_ru = F5TTS(
                model="F5TTS_v1_Base",
                ckpt_file=ckpt_path,
                vocab_file=vocab_path,
                device=self.device,
            )

            load_time = time.time() - load_start
            self._log(f"F5-TTS Russian model loaded in {load_time:.1f}s")
        else:
            self._log("Loading F5-TTS English model (F5TTS_v1_Base)...")
            load_start = time.time()

            self._tts_en = F5TTS(
                model="F5TTS_v1_Base",
                device=self.device,
            )

            load_time = time.time() - load_start
            self._log(f"F5-TTS English model loaded in {load_time:.1f}s")

        # CUDA memory optimization
        if self.device == "cuda":
            import torch
            torch.cuda.empty_cache()

    def _get_tts(self, lang: str):
        """Возвращает загруженную модель для языка."""
        if lang in RU_LANGS:
            self._load_model("ru")
            return self._tts_ru
        else:
            self._load_model("en")
            return self._tts_en

    def _get_accentizer(self):
        """Ленивая загрузка RUAccent (переиспользуется для всех сегментов)."""
        if self._accentizer is None:
            try:
                from ruaccent import RUAccent
                self._accentizer = RUAccent()
                self._accentizer.load(omograph_model_size='turbo', use_dictionary=True)
            except Exception as e:
                self._log(f"   Warning: RUAccent init error: {e}")
                return None
        return self._accentizer

    def _add_stress_marks(self, text: str) -> str:
        """Добавляет ударения в русский текст через RUAccent."""
        accentizer = self._get_accentizer()
        if accentizer is None:
            return text
        try:
            return accentizer.process_all(text)
        except Exception as e:
            self._log(f"   Warning: RUAccent error: {e}")
            return text

    def _preprocess_text(self, text: str, lang: str) -> str:
        """Предобработка текста перед TTS (ударения для русского)."""
        if lang in RU_LANGS:
            return self._add_stress_marks(text)
        return text

    def _run_tts(self, text: str, ref_file: str, ref_text: str,
                 lang: str = "en", seed: int = F5_SEED) -> Optional[np.ndarray]:
        """
        Генерирует речь через F5-TTS.

        Returns:
            numpy array с аудио (24kHz) или None при ошибке
        """
        tts = self._get_tts(lang)

        processed_text = self._preprocess_text(text, lang)
        processed_ref_text = self._preprocess_text(ref_text, lang)

        wav, sr, _ = tts.infer(
            ref_file=ref_file,
            ref_text=processed_ref_text,
            gen_text=processed_text,
            nfe_step=F5_NFE_STEP,
            cfg_strength=F5_CFG_STRENGTH,
            sway_sampling_coef=F5_SWAY_COEF,
            seed=seed,
        )

        if wav is None or len(wav) == 0:
            return None

        return wav

    # ── Intelligent Timing: TTS + Retry ────────────────────────────────────

    def _generate_with_timing(
        self,
        text: str,
        ref_file: str,
        ref_text: str,
        lang: str,
        time_budget_sec: float,
        segment_index: int,
    ) -> Tuple[Optional[np.ndarray], float, str]:
        """
        Генерирует TTS с учётом бюджета времени. Если слишком длинное —
        сокращает текст и перегенерирует.

        Стратегия:
          1. Генерируем TTS с полным текстом
          2. Если длительность <= budget * TIMING_TOLERANCE_RATIO → OK
             (VideoMaker справится через gentle atempo ≤ 1.08x)
          3. Если длиннее → сокращаем текст пропорционально и перегенерируем
          4. Максимум MAX_TIMING_RETRIES попыток

        Args:
            text: Текст для синтеза
            ref_file: Путь к референсному WAV
            ref_text: Транскрипция референса
            lang: Язык генерации
            time_budget_sec: Бюджет времени (effective_duration)
            segment_index: Индекс сегмента (для логов)

        Returns:
            Tuple (wav_array, actual_duration, final_text)
        """
        current_text = text
        max_acceptable = time_budget_sec * TIMING_TOLERANCE_RATIO

        for attempt in range(MAX_TIMING_RETRIES + 1):
            wav = self._run_tts(
                text=current_text,
                ref_file=ref_file,
                ref_text=ref_text,
                lang=lang,
            )

            if wav is None:
                return None, 0.0, current_text

            actual_duration = len(wav) / F5_SAMPLE_RATE

            # Проверяем: укладываемся ли в бюджет?
            if actual_duration <= max_acceptable:
                if attempt > 0:
                    self._log(
                        f"   Retry {attempt} OK: {actual_duration:.1f}s "
                        f"(budget: {time_budget_sec:.1f}s)"
                    )
                return wav, actual_duration, current_text

            # Превышение — нужно сокращать
            overshoot = actual_duration / time_budget_sec

            if attempt < MAX_TIMING_RETRIES:
                # Сокращаем текст пропорционально превышению
                target_ratio = time_budget_sec / actual_duration * 0.92  # 8% запас
                shortened = self._shorten_text_for_timing(current_text, target_ratio, lang)

                if shortened != current_text and len(shortened) >= MIN_TEXT_CHARS:
                    self._log(
                        f"   Seg {segment_index}: {actual_duration:.1f}s > "
                        f"{time_budget_sec:.1f}s budget ({overshoot:.0%}), "
                        f"retry {attempt+1} with shortened text "
                        f"({len(current_text)}→{len(shortened)} chars)"
                    )
                    current_text = shortened
                else:
                    # Не удалось сократить — возвращаем что есть
                    break
            else:
                # Все ретраи исчерпаны
                self._log(
                    f"   Seg {segment_index}: {actual_duration:.1f}s > "
                    f"{time_budget_sec:.1f}s budget ({overshoot:.0%}) "
                    f"after {MAX_TIMING_RETRIES} retries, "
                    f"VideoMaker will handle via atempo/trim"
                )

        return wav, actual_duration, current_text

    def _shorten_text_for_timing(self, text: str, target_ratio: float, lang: str) -> str:
        """
        Интеллектуально сокращает текст до target_ratio от текущей длины.

        Стратегии (в порядке приоритета):
          1. Убрать последнее предложение (если несколько)
          2. Убрать содержимое в скобках / вставные конструкции
          3. Убрать вводные слова / филлеры
          4. Обрезать по словам с сохранением завершённости

        Args:
            text: Исходный текст
            target_ratio: Целевое соотношение (0.0-1.0)
            lang: Язык текста

        Returns:
            Сокращённый текст
        """
        target_len = max(MIN_TEXT_CHARS, int(len(text) * target_ratio))

        # Стратегия 1: Убрать последнее предложение (если несколько)
        sentences = re.split(r'(?<=[.!?。！？])\s+', text.strip())
        if len(sentences) > 1:
            shorter = ' '.join(sentences[:-1])
            if len(shorter) >= target_len * 0.7:
                return shorter
            # Попробуем убрать 2 последних
            if len(sentences) > 2:
                shorter = ' '.join(sentences[:-2])
                if len(shorter) >= target_len * 0.7:
                    return shorter

        # Стратегия 2: Убрать содержимое в скобках и вставные конструкции
        cleaned = re.sub(r'\s*[\(\（][^)）]*[\)）]\s*', ' ', text)
        cleaned = re.sub(r'\s*[—–]\s*[^—–]*?[—–]\s*', ' ', cleaned)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        if len(cleaned) < len(text) * 0.95 and len(cleaned) >= target_len * 0.7:
            return cleaned

        # Стратегия 3: Обрезка по словам на границе предложения/клаузы
        words = text.split()
        target_words = max(3, int(len(words) * target_ratio))
        truncated = ' '.join(words[:target_words])

        # Пытаемся оборвать на знаке препинания
        for punct in ['.', '!', '?', '。', '！', '？', ',', ';', '，', '；']:
            last_pos = truncated.rfind(punct)
            if last_pos > len(truncated) * 0.5:
                return truncated[:last_pos + 1].strip()

        # Обрезаем на последнем полном слове
        return truncated.rstrip(',:;').strip()

    def _postprocess_audio(self, audio_path: str):
        """Постобработка: шумоподавление, нормализация, fade in/out."""
        try:
            # Noise reduction
            try:
                import noisereduce as nr
                from scipy.io import wavfile

                sample_rate, audio_data = wavfile.read(audio_path)
                if audio_data.dtype == np.int16:
                    audio_float = audio_data.astype(np.float32) / 32768.0
                elif audio_data.dtype == np.int32:
                    audio_float = audio_data.astype(np.float32) / 2147483648.0
                else:
                    audio_float = audio_data.astype(np.float32)

                reduced = nr.reduce_noise(
                    y=audio_float, sr=sample_rate,
                    stationary=True, prop_decrease=0.75,
                    n_fft=512, hop_length=128
                )
                wavfile.write(audio_path, sample_rate, (reduced * 32767).astype(np.int16))
            except ImportError:
                pass

            # Normalize + fade
            from pydub.effects import normalize as pydub_normalize
            audio = AudioSegment.from_file(audio_path)
            audio = pydub_normalize(audio, headroom=3.0)
            fade_ms = 30
            if len(audio) > fade_ms * 3:
                audio = audio.fade_in(fade_ms).fade_out(fade_ms)
            audio.export(audio_path, format="wav")
        except Exception as e:
            self._log(f"   Warning: postprocess error: {e}")

    # ── Speaker Sample Extraction ──────────────────────────────────────────

    def extract_speaker_samples(
        self,
        audio_path: str,
        segments: List[Dict]
    ) -> Dict[str, str]:
        """
        Извлекает референсные аудио для каждого уникального спикера.

        F5-TTS работает лучше с референсами до 12 секунд чистой речи.
        Также сохраняет текст референсного сегмента для точного ref_text.

        Returns:
            Словарь {speaker_id: path_to_sample.wav}
        """
        self._log("Extracting speaker reference audio...")

        if not segments:
            self._log("No segments to process")
            return {}

        try:
            audio = AudioSegment.from_file(audio_path)
            self._log(f"Source audio loaded: {len(audio) / 1000:.1f} sec")
        except Exception as e:
            self._log(f"Error loading audio: {e}")
            return {}

        # Группируем сегменты по спикерам
        speaker_segments = {}
        for seg in segments:
            speaker = seg.get("speaker", "SPEAKER_UNKNOWN")
            if speaker not in speaker_segments:
                speaker_segments[speaker] = []
            speaker_segments[speaker].append(seg)

        self._log(f"Found {len(speaker_segments)} speakers")

        speaker_samples = {}

        IDEAL_SEGMENT_DURATION = (3.0, 12.0)

        for speaker, segs in speaker_segments.items():
            sorted_segs = sorted(
                segs,
                key=lambda s: float(s.get("end", 0)) - float(s.get("start", 0)),
                reverse=True
            )

            # Стратегия 1: один идеальный сегмент (3-12 секунд)
            best_single_seg = None
            for seg in sorted_segs:
                duration = float(seg.get("end", 0)) - float(seg.get("start", 0))
                if IDEAL_SEGMENT_DURATION[0] <= duration <= IDEAL_SEGMENT_DURATION[1]:
                    best_single_seg = seg
                    break

            if best_single_seg:
                start_ms = int(best_single_seg.get("start", 0) * 1000)
                end_ms = int(best_single_seg.get("end", 0) * 1000)
                duration = (end_ms - start_ms) / 1000.0

                try:
                    sample_audio = audio[start_ms:end_ms]
                    sample_audio = self._enhance_reference_audio(sample_audio)

                    sample_path = self.voices_dir / f"{speaker}_sample.wav"
                    sample_audio.export(str(sample_path), format="wav",
                                       parameters=["-ar", "24000", "-ac", "1"])

                    speaker_samples[speaker] = str(sample_path)

                    ref_text = best_single_seg.get("text", "").strip()
                    if ref_text:
                        self._speaker_ref_texts[speaker] = ref_text

                    self._log(f"   {speaker}: {duration:.1f}s reference (ideal segment)")
                    continue
                except Exception as e:
                    self._log(f"   Warning: error extracting for {speaker}: {e}")

            # Стратегия 2: объединяем несколько сегментов
            combined_audio = AudioSegment.empty()
            combined_duration = 0.0
            segments_used = 0
            combined_texts = []

            for seg in sorted_segs:
                if combined_duration >= MAX_REF_DURATION_SEC:
                    break

                start_ms = int(seg.get("start", 0) * 1000)
                end_ms = int(seg.get("end", 0) * 1000)
                seg_duration = (end_ms - start_ms) / 1000.0

                if seg_duration < 1.0:
                    continue

                try:
                    seg_audio = audio[start_ms:end_ms]
                    if len(combined_audio) > 0:
                        combined_audio += AudioSegment.silent(duration=200)
                    combined_audio += seg_audio
                    combined_duration += seg_duration
                    segments_used += 1

                    seg_text = seg.get("text", "").strip()
                    if seg_text:
                        combined_texts.append(seg_text)
                except Exception:
                    continue

            if combined_duration < 2.0:
                self._log(f"   Warning: not enough audio for {speaker} ({combined_duration:.1f}s)")
                continue

            if combined_duration > MAX_REF_DURATION_SEC:
                combined_audio = combined_audio[:int(MAX_REF_DURATION_SEC * 1000)]
                combined_duration = MAX_REF_DURATION_SEC

            try:
                combined_audio = self._enhance_reference_audio(combined_audio)

                sample_path = self.voices_dir / f"{speaker}_sample.wav"
                combined_audio.export(str(sample_path), format="wav",
                                     parameters=["-ar", "24000", "-ac", "1"])

                speaker_samples[speaker] = str(sample_path)

                if combined_texts:
                    self._speaker_ref_texts[speaker] = " ".join(combined_texts)

                self._log(f"   {speaker}: {combined_duration:.1f}s reference ({segments_used} segments combined)")
            except Exception as e:
                self._log(f"   Error saving reference for {speaker}: {e}")
                continue

        self._log(f"Extracted {len(speaker_samples)}/{len(speaker_segments)} speaker references")
        return speaker_samples

    def _enhance_reference_audio(self, audio: AudioSegment) -> AudioSegment:
        """Улучшает качество референсного аудио."""
        try:
            from pydub.effects import normalize, strip_silence, compress_dynamic_range

            if audio.channels > 1:
                audio = audio.set_channels(1)

            audio = normalize(audio)
            audio = strip_silence(audio, silence_len=50, silence_thresh=-40, padding=50)

            try:
                audio = compress_dynamic_range(audio, threshold=-20.0, ratio=2.0,
                                               attack=5.0, release=50.0)
            except Exception:
                pass

            return audio
        except ImportError:
            return audio
        except Exception:
            return audio

    def extract_speaker_samples_for_gender(
        self,
        audio_path: str,
        segments: List[Dict]
    ) -> Dict[str, str]:
        """Быстрая извлечка минимальных аудио-сэмплов только для определения пола."""
        self._log("Quick extraction for gender detection...")

        if not segments:
            return {}

        try:
            audio = AudioSegment.from_file(audio_path)
        except Exception as e:
            self._log(f"Error loading audio: {e}")
            return {}

        speaker_segments = {}
        for seg in segments:
            speaker = seg.get("speaker", "SPEAKER_UNKNOWN")
            if speaker not in speaker_segments:
                speaker_segments[speaker] = []
            speaker_segments[speaker].append(seg)

        speaker_samples = {}

        for speaker, segs in speaker_segments.items():
            sorted_segs = sorted(
                segs,
                key=lambda s: float(s.get("end", 0)) - float(s.get("start", 0)),
                reverse=True
            )

            for seg in sorted_segs:
                start_ms = int(seg.get("start", 0) * 1000)
                end_ms = int(seg.get("end", 0) * 1000)
                duration = (end_ms - start_ms) / 1000.0

                if duration < 2.0:
                    continue

                try:
                    sample_audio = audio[start_ms:end_ms]
                    sample_audio = sample_audio.set_channels(1)

                    sample_path = self.voices_dir / f"{speaker}_gender_sample.wav"
                    sample_audio.export(str(sample_path), format="wav",
                                       parameters=["-ar", "16000", "-ac", "1"])
                    speaker_samples[speaker] = str(sample_path)
                    self._log(f"   {speaker}: {duration:.1f}s sample for analysis")
                    break
                except Exception:
                    continue

            if speaker not in speaker_samples:
                self._log(f"   Warning: {speaker}: not enough audio for gender detection")

        return speaker_samples

    # ── Preset Voices (готовые голоса) ──────────────────────────────────────

    _PRESET_TEXT_MALE = (
        "Hello everyone, today we will look at a very interesting topic "
        "that concerns each of us. Let's understand the details and try "
        "to grasp the basic principles of this matter."
    )
    _PRESET_TEXT_FEMALE = (
        "Hello dear friends, I am glad to welcome you to our channel. "
        "Today we have an important and fascinating topic. I hope you "
        "will find it interesting and useful to learn more about it."
    )

    def _get_presets_dir(self) -> Path:
        presets_dir = self.voices_dir / "presets"
        presets_dir.mkdir(parents=True, exist_ok=True)
        return presets_dir

    def _ensure_preset_voices(self) -> Dict[str, str]:
        """Проверяет наличие пресетных голосов и генерирует через edge-tts если нет."""
        presets_dir = self._get_presets_dir()
        male_path = presets_dir / "male_preset.wav"
        female_path = presets_dir / "female_preset.wav"

        if male_path.exists() and female_path.exists():
            self._log("Preset voices found in cache")
            return {"male": str(male_path), "female": str(female_path)}

        self._log("Generating preset voices via Microsoft Edge TTS...")

        try:
            import edge_tts
        except ImportError:
            self._log("Installing edge-tts...")
            import subprocess
            subprocess.check_call([sys.executable, "-m", "pip", "install", "edge-tts"])
            import edge_tts

        import asyncio

        async def _generate_preset(voice: str, text: str, output_path: Path):
            mp3_path = output_path.with_suffix(".mp3")
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(str(mp3_path))
            audio = AudioSegment.from_mp3(str(mp3_path))
            audio = audio.set_channels(1).set_frame_rate(24000)
            audio.export(str(output_path), format="wav")
            mp3_path.unlink(missing_ok=True)

        async def _generate_all():
            if not male_path.exists():
                self._log("Generating male voice (en-US-GuyNeural)...")
                await _generate_preset("en-US-GuyNeural", self._PRESET_TEXT_MALE, male_path)
            if not female_path.exists():
                self._log("Generating female voice (en-US-JennyNeural)...")
                await _generate_preset("en-US-JennyNeural", self._PRESET_TEXT_FEMALE, female_path)

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    pool.submit(lambda: asyncio.run(_generate_all())).result()
            else:
                loop.run_until_complete(_generate_all())
        except RuntimeError:
            asyncio.run(_generate_all())

        return {"male": str(male_path), "female": str(female_path)}

    def _detect_gender(self, audio_path: str) -> str:
        """
        Определяет пол спикера по высоте голоса (F0).
        Мужской: F0 ~ 85-155 Гц, Женский: F0 ~ 165-255 Гц, Порог: 160 Гц
        """
        try:
            audio = AudioSegment.from_file(audio_path)
            audio = audio.set_channels(1).set_frame_rate(16000).set_sample_width(2)

            samples = np.array(struct.unpack(
                f"<{len(audio.raw_data) // 2}h", audio.raw_data
            ), dtype=np.float64)

            if len(samples) < 1600:
                return "male"

            samples = samples / (np.max(np.abs(samples)) + 1e-10)

            sample_rate = 16000
            min_lag = sample_rate // 300
            max_lag = sample_rate // 60
            frame_size = int(sample_rate * 0.05)
            hop_size = int(sample_rate * 0.02)

            f0_values = []
            for start in range(0, len(samples) - frame_size, hop_size):
                frame = samples[start:start + frame_size]
                energy = np.mean(frame ** 2)
                if energy < 0.001:
                    continue

                corr = np.correlate(frame, frame, mode='full')
                corr = corr[len(corr) // 2:]

                if max_lag >= len(corr):
                    continue
                if corr[0] <= 0:
                    continue
                corr_norm = corr / corr[0]

                search_region = corr_norm[min_lag:max_lag + 1]
                if len(search_region) == 0:
                    continue

                peak_idx = np.argmax(search_region) + min_lag
                peak_val = corr_norm[peak_idx]

                if peak_val < 0.25:
                    continue

                double_lag = peak_idx * 2
                if double_lag < len(corr_norm):
                    search_lo = max(min_lag, double_lag - 3)
                    search_hi = min(max_lag, double_lag + 3)
                    if search_lo < search_hi and search_hi < len(corr_norm):
                        sub_region = corr_norm[search_lo:search_hi + 1]
                        sub_peak_val = np.max(sub_region)
                        if sub_peak_val > peak_val * 0.7:
                            sub_peak_idx = np.argmax(sub_region) + search_lo
                            peak_idx = sub_peak_idx

                f0 = sample_rate / peak_idx
                if 60 <= f0 <= 300:
                    f0_values.append(f0)

            if not f0_values:
                return "male"

            median_f0 = float(np.median(f0_values))
            gender = "female" if median_f0 > 160 else "male"
            self._log(f"   F0 = {median_f0:.0f} Hz -> {'female' if gender == 'female' else 'male'}")
            return gender

        except Exception as e:
            self._log(f"   Warning: gender detection error: {e}, defaulting to male")
            return "male"

    def _create_pitch_variant(self, wav_path: str, semitones: float) -> str:
        """Создаёт вариант голоса со сдвигом высоты тона."""
        try:
            audio = AudioSegment.from_file(wav_path)
            original_rate = audio.frame_rate
            shift_factor = 2 ** (semitones / 12.0)
            new_rate = int(original_rate * shift_factor)

            shifted = audio._spawn(audio.raw_data, overrides={"frame_rate": new_rate})
            shifted = shifted.set_frame_rate(original_rate)

            variant_name = Path(wav_path).stem + f"_variant_{semitones:+.1f}st.wav"
            variant_path = self._get_presets_dir() / variant_name
            shifted.export(str(variant_path), format="wav",
                          parameters=["-ar", "24000", "-ac", "1"])
            return str(variant_path)

        except Exception as e:
            self._log(f"   Warning: pitch variant error: {e}")
            return wav_path

    def _build_preset_speaker_map(
        self,
        speaker_samples: Dict[str, str],
        preset_voices: Dict[str, str]
    ) -> Dict[str, str]:
        """Строит маппинг спикеров на пресетные голоса с учётом пола."""
        self._log("Detecting speaker genders...")

        speaker_genders = {}
        for speaker, sample_path in speaker_samples.items():
            gender = self._detect_gender(sample_path)
            speaker_genders[speaker] = gender

        male_speakers = [s for s, g in speaker_genders.items() if g == "male"]
        female_speakers = [s for s, g in speaker_genders.items() if g == "female"]

        preset_map = {}
        pitch_variants = [0, +1.5, -1.5, +2.0, -2.0]

        for i, speaker in enumerate(male_speakers):
            if i == 0:
                preset_map[speaker] = preset_voices["male"]
            else:
                semitones = pitch_variants[min(i, len(pitch_variants) - 1)]
                variant = self._create_pitch_variant(preset_voices["male"], semitones)
                preset_map[speaker] = variant

        for i, speaker in enumerate(female_speakers):
            if i == 0:
                preset_map[speaker] = preset_voices["female"]
            else:
                semitones = pitch_variants[min(i, len(pitch_variants) - 1)]
                variant = self._create_pitch_variant(preset_voices["female"], semitones)
                preset_map[speaker] = variant

        if not preset_map:
            preset_map["SPEAKER_UNKNOWN"] = preset_voices["male"]

        self._log(f"Preset voices assigned: {len(male_speakers)} male, {len(female_speakers)} female")
        return preset_map

    # ── Main Dubbing Generation ────────────────────────────────────────────

    def generate_dubbing(
        self,
        segments: List[Dict],
        speaker_samples: Dict[str, str],
        target_lang: str = "ru",
        use_preset_voices: bool = False
    ) -> List[Dict]:
        """
        Генерирует дубляж для всех сегментов с клонированием голоса.

        Интеллектуальная система:
          1. Вычисляет бюджет времени (elastic timing)
          2. Генерирует TTS с проверкой длительности
          3. При превышении: сокращает текст + перегенерирует
          4. Сохраняет actual_audio_duration для VideoMaker
        """
        if not segments:
            self._log("No segments for dubbing")
            return segments

        # Нормализуем язык
        lang_map = {
            'RUSSIAN': 'ru', 'ENGLISH': 'en', 'SPANISH': 'es', 'FRENCH': 'fr',
            'GERMAN': 'de', 'ITALIAN': 'it', 'PORTUGUESE': 'pt', 'POLISH': 'pl',
            'TURKISH': 'tr', 'DUTCH': 'nl', 'CZECH': 'cs', 'ARABIC': 'ar',
            'CHINESE': 'zh', 'HUNGARIAN': 'hu', 'KOREAN': 'ko', 'JAPANESE': 'ja',
            'HINDI': 'hi'
        }
        target_lang = lang_map.get(target_lang.upper(), target_lang.lower())

        # Загружаем модель
        self._load_model(target_lang)

        # Если preset — подменяем speaker_samples
        if use_preset_voices:
            self._log("Preset mode: using professional preset voices")
            try:
                preset_voices = self._ensure_preset_voices()
                speaker_samples = self._build_preset_speaker_map(speaker_samples, preset_voices)
            except Exception as e:
                self._log(f"Error preparing preset voices: {e}")
                self._log("Falling back to voice cloning")

        if not speaker_samples:
            self._log("No speaker references available")
            return segments

        total_segments = len(segments)
        self._log(f"Generating dubbing for {total_segments} segments (F5-TTS, {target_lang})...")

        # ── Elastic Timing: вычисляем бюджеты времени ──────────────────────
        from core.elastic_timing import compute_effective_durations
        compute_effective_durations(segments)
        extensions = [s.get('gap_extension', 0) for s in segments if s.get('gap_extension', 0) > 0]
        if extensions:
            self._log(
                f"   Elastic timing: {len(extensions)} segments extended, "
                f"avg +{sum(extensions)/len(extensions):.2f}s, max +{max(extensions):.2f}s"
            )

        fallback_sample = list(speaker_samples.values())[0] if speaker_samples else None

        # Ref texts per speaker
        # При кросс-язычном дубляже ref_text должен быть на языке target,
        # иначе F5-TTS галлюцинирует (артефакты, лишние слова).
        _generic_ref_texts = {
            "en": "This is a sample of my voice for cloning purposes.",
            "ru": "Это образец моего голоса для клонирования.",
            "es": "Esta es una muestra de mi voz para fines de clonación.",
            "fr": "Ceci est un échantillon de ma voix à des fins de clonage.",
            "de": "Dies ist eine Probe meiner Stimme zum Klonen.",
            "it": "Questo è un campione della mia voce per la clonazione.",
            "pt": "Esta é uma amostra da minha voz para fins de clonagem.",
            "ja": "これは音声クローン用のサンプルです。",
            "ko": "이것은 음성 복제를 위한 샘플입니다.",
            "zh": "这是我的声音克隆样本。",
        }
        _default_ref_text = _generic_ref_texts.get(target_lang, _generic_ref_texts["en"])

        # Определяем исходный язык из первых сегментов (original_text)
        _source_lang = None
        for seg in segments[:5]:
            orig = seg.get("original_text", "")
            if orig:
                if any('\u0400' <= c <= '\u04FF' for c in orig):
                    _source_lang = "ru"
                elif any('\u4e00' <= c <= '\u9fff' for c in orig):
                    _source_lang = "zh"
                elif any('\u3040' <= c <= '\u309f' or '\u30a0' <= c <= '\u30ff' for c in orig):
                    _source_lang = "ja"
                elif any('\uac00' <= c <= '\ud7af' for c in orig):
                    _source_lang = "ko"
                else:
                    _source_lang = "en"
                break

        _cross_lingual = _source_lang is not None and _source_lang != target_lang
        if _cross_lingual:
            self._log(f"   Cross-lingual dubbing: {_source_lang} → {target_lang}, using generic ref_text")

        updated_segments = []
        success_count = 0
        error_count = 0
        retry_count = 0
        start_time = time.time()

        for i, seg in enumerate(segments):
            # Проверяем флаг остановки
            if self.should_stop_callback and self.should_stop_callback():
                self._log("Dubbing generation stopped by user")
                raise InterruptedError("Processing stopped by user")

            speaker = seg.get("speaker", "SPEAKER_UNKNOWN")
            text = seg.get("text", "").strip()
            original_duration = float(seg.get("end", 0)) - float(seg.get("start", 0))
            effective_duration = float(seg.get("effective_duration", original_duration))
            gap_ext = float(seg.get("gap_extension", 0.0))

            if not text:
                updated_segments.append(seg)
                continue

            speaker_wav = speaker_samples.get(speaker, fallback_sample)

            if not speaker_wav or not os.path.exists(speaker_wav):
                self._log(f"[{i+1}/{total_segments}] No reference for {speaker}, skipping")
                updated_segments.append(seg)
                error_count += 1
                continue

            output_path = self.temp_tts_dir / f"segment_{i:04d}.wav"

            try:
                progress = ((i + 1) / total_segments) * 100
                elapsed = time.time() - start_time
                avg_time = elapsed / (i + 1) if i > 0 else 0
                remaining = avg_time * (total_segments - (i + 1))

                ext_info = f" +{gap_ext:.1f}s" if gap_ext > 0 else ""
                self._log(
                    f"[{i+1}/{total_segments}] ({progress:.0f}%) {speaker} | "
                    f"{len(text)} chars | budget: {effective_duration:.1f}s{ext_info} | "
                    f"~{remaining/60:.1f} min left"
                )

                seg_start = time.time()

                # ref_text для спикера
                # При кросс-язычном дубляже всегда используем generic ref_text
                # на целевом языке, чтобы F5-TTS не галлюцинировал от
                # несовпадения языка ref_text и gen_text
                if _cross_lingual:
                    ref_text = _default_ref_text
                else:
                    ref_text = self._speaker_ref_texts.get(speaker, _default_ref_text)

                # ── Timing-aware generation with retry ──────────────────
                wav, actual_duration, final_text = self._generate_with_timing(
                    text=text,
                    ref_file=speaker_wav,
                    ref_text=ref_text,
                    lang=target_lang,
                    time_budget_sec=effective_duration,
                    segment_index=i,
                )

                if wav is None:
                    raise RuntimeError("No audio generated")

                # Отслеживаем ретраи (если текст изменился)
                if final_text != text:
                    retry_count += 1

                sf.write(str(output_path), wav, F5_SAMPLE_RATE)

                # Постобработка
                self._postprocess_audio(str(output_path))

                seg_time = time.time() - seg_start

                # Определяем статус тайминга
                pressure = actual_duration / effective_duration if effective_duration > 0 else 0
                if pressure <= 1.0:
                    timing_status = "perfect"
                elif pressure <= TIMING_TOLERANCE_RATIO:
                    timing_status = "OK (gentle atempo)"
                elif pressure <= MAX_ATEMPO_TOLERANCE:
                    timing_status = "tight (atempo)"
                else:
                    timing_status = "OVER (will trim)"

                self._log(
                    f"   {actual_duration:.1f}s / {effective_duration:.1f}s "
                    f"({pressure:.0%}) [{timing_status}] in {seg_time:.1f}s"
                )

                seg_copy = seg.copy()
                seg_copy["audio_file"] = str(output_path)
                seg_copy["actual_audio_duration"] = actual_duration
                if final_text != text:
                    seg_copy["text_shortened"] = True
                    seg_copy["text"] = final_text
                updated_segments.append(seg_copy)
                success_count += 1

            except InterruptedError:
                raise
            except Exception as e:
                self._log(f"[{i+1}/{total_segments}] Error for segment {i} ({speaker}): {e}")
                updated_segments.append(seg)
                error_count += 1
                continue

        # Очищаем CUDA memory
        if self.device == "cuda":
            import torch
            torch.cuda.empty_cache()

        total_time = time.time() - start_time

        # Итоговая статистика
        self._log(
            f"Dubbing complete: {success_count}/{total_segments} OK, "
            f"{error_count} errors, {retry_count} retried | "
            f"Total: {total_time/60:.1f} min"
        )

        # Статистика тайминга
        from core.elastic_timing import estimate_timing_pressure
        pressure_stats = estimate_timing_pressure(updated_segments)
        if pressure_stats['avg_pressure'] > 0:
            self._log(
                f"   Timing pressure: avg {pressure_stats['avg_pressure']:.0%}, "
                f"max {pressure_stats['max_pressure']:.0%}, "
                f"{pressure_stats['over_budget_count']} over-budget "
                f"(total deficit: {pressure_stats['over_budget_total_sec']:.1f}s)"
            )

        return updated_segments

    # ── Audio Assembly ─────────────────────────────────────────────────────

    def merge_audio_segments(
        self,
        segments: List[Dict],
        output_path: str
    ) -> str:
        """Объединяет все аудио сегменты в один файл."""
        self._log(f"Merging {len(segments)} audio segments...")

        if not segments:
            raise ValueError("No segments to merge")

        audio_segments = []
        missing_files = []

        for i, seg in enumerate(segments):
            audio_file = seg.get("audio_file")
            if not audio_file or not os.path.exists(audio_file):
                missing_files.append(i)
                start = float(seg.get("start", 0))
                end = float(seg.get("end", start + 1.0))
                duration_ms = int((end - start) * 1000)
                silence = AudioSegment.silent(duration=duration_ms)
                audio_segments.append(silence)
            else:
                try:
                    audio = AudioSegment.from_file(audio_file)
                    audio_segments.append(audio)
                except Exception as e:
                    self._log(f"   Warning: error loading segment {i}: {e}")
                    start = float(seg.get("start", 0))
                    end = float(seg.get("end", start + 1.0))
                    duration_ms = int((end - start) * 1000)
                    silence = AudioSegment.silent(duration=duration_ms)
                    audio_segments.append(silence)

        if missing_files:
            self._log(f"   {len(missing_files)} missing files replaced with silence")

        if not audio_segments:
            raise ValueError("No audio to merge")

        CROSSFADE_MS = 30

        final_audio = audio_segments[0]
        if len(final_audio) > CROSSFADE_MS * 3:
            final_audio = final_audio.fade_in(CROSSFADE_MS)

        for seg in audio_segments[1:]:
            safe_crossfade = min(CROSSFADE_MS, len(seg) // 3, len(final_audio) // 3)
            if safe_crossfade > 0:
                final_audio = final_audio.append(seg, crossfade=safe_crossfade)
            else:
                final_audio = final_audio + seg

        if len(final_audio) > CROSSFADE_MS * 3:
            final_audio = final_audio.fade_out(CROSSFADE_MS)

        output_path_obj = Path(output_path)
        output_path_obj.parent.mkdir(parents=True, exist_ok=True)

        final_audio.export(str(output_path), format="wav")

        duration_sec = len(final_audio) / 1000.0
        self._log(f"Final audio created: {output_path} ({duration_sec:.1f}s)")

        return str(output_path)

    def cleanup_temp_files(self):
        """Очищает временные файлы TTS"""
        try:
            if self.temp_tts_dir.exists():
                for file in self.temp_tts_dir.glob("*.wav"):
                    file.unlink()
                self._log("Temp TTS files cleaned up")
        except Exception as e:
            self._log(f"Warning: cleanup error: {e}")

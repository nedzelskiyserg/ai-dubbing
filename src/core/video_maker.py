# -*- coding: utf-8 -*-
"""
Video Maker Module — профессиональная сборка дублированного видео.

Подход на основе Linly-Dubbing:
  1. Demucs separation: TTS-дорожка + инструменты (без конфликта двух голосов)
  2. Pitch-preserving time-stretch (0.90x — 1.10x) через FFmpeg atempo
  3. Volume matching: TTS нормализуется к уровню оригинальных вокалов
  4. Overlap prevention: каждый сегмент ограничен началом следующего
  5. Adaptive Timing: бюджеты с учётом фактических длительностей TTS
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Dict, Optional, Callable
from pydub import AudioSegment
from pydub.silence import detect_silence
import numpy as np
import logging
from core.config import APP_PATHS, resolve_path_for_win
from core.elastic_timing import compute_adaptive_durations, estimate_timing_pressure

# Пытаемся импортировать moviepy
MOVIEPY_AVAILABLE = False
MOVIEPY_CHECKED = False

def _check_moviepy_installed() -> bool:
    """Проверяет, установлен ли MoviePy"""
    global MOVIEPY_AVAILABLE
    try:
        # MoviePy 2.x использует прямой импорт из moviepy
        try:
            from moviepy import VideoFileClip, AudioFileClip
            MOVIEPY_AVAILABLE = True
            return True
        except ImportError:
            # Fallback для MoviePy 1.x
            from moviepy.editor import VideoFileClip, AudioFileClip
            MOVIEPY_AVAILABLE = True
            return True
    except ImportError:
        MOVIEPY_AVAILABLE = False
        return False

def _install_moviepy() -> bool:
    """Устанавливает MoviePy через pip"""
    try:
        logger.info("📦 Установка moviepy...")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "moviepy"],
            capture_output=True,
            text=True,
            timeout=180
        )
        
        if result.returncode == 0:
            logger.info("✅ moviepy успешно установлен")
            # Обновляем глобальную переменную после установки
            global MOVIEPY_AVAILABLE
            MOVIEPY_AVAILABLE = _check_moviepy_installed()
            return MOVIEPY_AVAILABLE
        else:
            logger.error(f"❌ Ошибка установки moviepy: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        logger.error("❌ Таймаут при установке moviepy")
        return False
    except Exception as e:
        logger.error(f"❌ Ошибка при установке moviepy: {e}")
        return False

# Первоначальная проверка
if not _check_moviepy_installed():
    MOVIEPY_AVAILABLE = False

logger = logging.getLogger(__name__)


class VideoMaker:
    """
    Профессиональная сборка дублированного видео.

    Подход (на основе Linly-Dubbing + собственные улучшения):
    - Demucs: чистые инструменты + TTS (без конфликта двух голосов)
    - Bi-directional stretch: замедление 0.90x — ускорение 1.10x (pitch-preserving)
    - Volume matching: TTS нормализуется к уровню оригинальных вокалов
    - Overlap prevention: каждый сегмент ограничен началом следующего
    - Adaptive timing: бюджеты с учётом фактических длительностей TTS
    """

    # ── Параметры time-stretch ───────────────────────────────────────────
    MAX_ATEMPO_SPEED = 1.10          # Макс ускорение (pitch-preserving)
    MIN_ATEMPO_SPEED = 0.90          # Мин замедление (заполняет слот, не растягивает сильно)
    ATEMPO_COMFORT_ZONE = 1.06       # До этого — ускоряем без колебаний
    SLOWDOWN_THRESHOLD = 0.80        # ratio < 0.80 → замедляем до MIN_ATEMPO_SPEED

    # ── Общие параметры ──────────────────────────────────────────────────
    SEGMENT_FADE_MS = 50             # Fade in/out для стыков
    TRIM_FADE_SEC = 0.050            # Fade-out после FFmpeg trim

    # ── Smart trim ───────────────────────────────────────────────────────
    SMART_TRIM_SEARCH_MS = 500       # Окно поиска тишины перед точкой обрезки
    SMART_TRIM_MIN_SILENCE_MS = 80   # Минимальная длительность тишины
    SMART_TRIM_SILENCE_THRESH_DB = -35  # Порог тишины (dBFS)
    
    def __init__(
        self,
        temp_dir: Optional[str] = None,
        progress_callback: Optional[Callable[[str], None]] = None,
        should_stop_callback: Optional[Callable[[], bool]] = None
    ):
        """
        Инициализация VideoMaker.
        
        Args:
            temp_dir: Директория для временных файлов
            progress_callback: Функция для логирования прогресса
        """
        if temp_dir:
            self.temp_dir = Path(temp_dir)
        else:
            self.temp_dir = APP_PATHS['temp']
            
        try:
            self.temp_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            # Fallback to system temp if permissions fail
            import tempfile
            self.temp_dir = Path(tempfile.gettempdir()) / "ai_dubbing_temp"
            self.temp_dir.mkdir(parents=True, exist_ok=True)
            
        self.progress_callback = progress_callback or (lambda msg: None)
        self.should_stop_callback = should_stop_callback
        
        # Директория для обработанных аудио
        self.processed_audio_dir = self.temp_dir / "processed_audio"
        self.processed_audio_dir.mkdir(parents=True, exist_ok=True)
        
        self._log("🎬 VideoMaker инициализирован")
    
    def _log(self, msg: str):
        """Логирование в UI и консоль"""
        print(msg)
        if self.progress_callback:
            self.progress_callback(msg)
        logger.info(msg)

    def _get_ffmpeg_path(self) -> Optional[str]:
        """Ищет путь к FFmpeg"""
        import shutil

        # Сначала проверяем переменную окружения FFMPEG_PATH (передаётся из Electron)
        env_path = os.environ.get('FFMPEG_PATH', '')
        if env_path and os.path.isfile(env_path):
            return env_path

        # Проверяем %LOCALAPPDATA%/AI Dubbing Studio/ffmpeg/ (установлено через dependency-manager)
        if os.name == 'nt':
            local_appdata = os.environ.get('LOCALAPPDATA', '')
            if local_appdata:
                app_ffmpeg = os.path.join(local_appdata, 'AI Dubbing Studio', 'ffmpeg', 'ffmpeg.exe')
                if os.path.isfile(app_ffmpeg):
                    return app_ffmpeg

        ffmpeg_path = shutil.which("ffmpeg")
        if ffmpeg_path:
            return ffmpeg_path

        # Проверяем стандартные пути
        possible_paths = [
            "/usr/local/bin/ffmpeg",
            "/opt/homebrew/bin/ffmpeg",
            "/usr/bin/ffmpeg",
        ]

        for path in possible_paths:
            if os.path.exists(path) and os.access(path, os.X_OK):
                return path

        return None
    
    def _get_audio_duration(self, audio_path: str) -> float:
        """
        Получает длительность аудио файла в секундах.
        
        Args:
            audio_path: Путь к аудио файлу
            
        Returns:
            Длительность в секундах
        """
        try:
            audio = AudioSegment.from_file(audio_path)
            duration_sec = len(audio) / 1000.0
            return duration_sec
        except Exception as e:
            self._log(f"⚠️ Ошибка получения длительности {audio_path}: {e}")
            # Fallback: используем ffprobe
            try:
                ffmpeg_path = self._get_ffmpeg_path()
                if not ffmpeg_path:
                    raise Exception("FFmpeg не найден")
                
                ffprobe_path = ffmpeg_path.replace("ffmpeg", "ffprobe")
                if not os.path.exists(ffprobe_path):
                    ffprobe_path = ffprobe_path.replace("ffprobe", "ffprobe")
                
                cmd = [
                    ffprobe_path,
                    "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    audio_path
                ]
                
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                if result.returncode == 0:
                    duration = float(result.stdout.strip())
                    return duration
                else:
                    raise Exception(f"ffprobe error: {result.stderr}")
            except Exception as probe_err:
                self._log(f"⚠️ Ошибка ffprobe: {probe_err}")
                return 0.0
    
    def _fit_audio_to_slot(
        self,
        audio_path: str,
        target_duration_sec: float,
        segment_index: int,
        actual_audio_duration: float = 0.0
    ) -> str:
        """
        Bi-directional pitch-preserving time-stretch (подход Linly-Dubbing).

        Стратегия:
          1. ratio ∈ [0.90, 1.0] → без изменений (допустимо короче)
          2. ratio < SLOWDOWN_THRESHOLD (0.80) → замедление до MIN_ATEMPO_SPEED (0.90x)
          3. ratio ∈ (1.0, 1.06] → мягкое ускорение
          4. ratio ∈ (1.06, 1.10] → ускорение
          5. ratio > 1.10 → ускорение 1.10x + smart trim

        FFmpeg atempo: pitch-preserving, диапазон 0.5-2.0.

        Args:
            audio_path: Путь к исходному аудио файлу
            target_duration_sec: Целевая длительность в секундах
            segment_index: Индекс сегмента
            actual_audio_duration: Фактическая длительность из VoiceCloner (0 = измерить)

        Returns:
            Путь к обработанному аудио файлу
        """
        if not os.path.exists(audio_path):
            return audio_path

        current_duration = actual_audio_duration if actual_audio_duration > 0 else self._get_audio_duration(audio_path)
        if current_duration <= 0:
            return audio_path

        # speed_factor > 1.0 → аудио длиннее слота (нужно ускорить)
        # speed_factor < 1.0 → аудио короче слота (можно замедлить)
        speed_factor = current_duration / target_duration_sec
        ratio = current_duration / target_duration_sec

        # ── Аудио вписывается с допуском (90%-100% слота) — не трогаем ──
        if self.MIN_ATEMPO_SPEED <= speed_factor <= 1.0:
            return audio_path

        # ── Аудио значительно короче слота → замедляем (pitch-preserving) ──
        if speed_factor < self.MIN_ATEMPO_SPEED and ratio < self.SLOWDOWN_THRESHOLD:
            # Замедляем, но не ниже MIN_ATEMPO_SPEED
            slow_factor = max(speed_factor, self.MIN_ATEMPO_SPEED)

            ffmpeg_path = self._get_ffmpeg_path()
            if not ffmpeg_path:
                return audio_path

            processed_path = self.processed_audio_dir / f"segment_{segment_index:04d}_processed.wav"
            filter_chain = f"atempo={slow_factor:.4f}"

            self._log(
                f"   Сегмент {segment_index}: замедление {slow_factor:.3f}x "
                f"({current_duration:.2f}s → {current_duration/slow_factor:.2f}s, слот {target_duration_sec:.2f}s)"
            )

            try:
                cmd = [ffmpeg_path, "-i", audio_path, "-af", filter_chain, "-y", str(processed_path)]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                if result.returncode == 0 and self._get_audio_duration(str(processed_path)) > 0:
                    return str(processed_path)
            except Exception:
                pass
            return audio_path

        # ── Аудио короче слота, но в пределах допуска — не трогаем ──
        if speed_factor < 1.0:
            return audio_path

        # ── Аудио длиннее слота → ускоряем ──
        ffmpeg_path = self._get_ffmpeg_path()
        if not ffmpeg_path:
            return audio_path

        processed_path = self.processed_audio_dir / f"segment_{segment_index:04d}_processed.wav"

        if speed_factor <= self.ATEMPO_COMFORT_ZONE:
            # Мягкое ускорение
            filter_chain = f"atempo={speed_factor:.4f}"
            self._log(
                f"   Сегмент {segment_index}: ускорение {speed_factor:.3f}x "
                f"({current_duration:.2f}s → {target_duration_sec:.2f}s)"
            )

        elif speed_factor <= self.MAX_ATEMPO_SPEED:
            # Ускорение на пределе
            filter_chain = f"atempo={speed_factor:.4f}"
            self._log(
                f"   Сегмент {segment_index}: ускорение {speed_factor:.3f}x "
                f"({current_duration:.2f}s → {target_duration_sec:.2f}s)"
            )

        else:
            # Ускорение MAX + trim
            trim_amount = current_duration / self.MAX_ATEMPO_SPEED - target_duration_sec
            fade_start = max(0, target_duration_sec - self.TRIM_FADE_SEC)
            filter_chain = (
                f"atempo={self.MAX_ATEMPO_SPEED:.3f},"
                f"atrim=0:{target_duration_sec:.3f},"
                f"afade=t=out:st={fade_start:.3f}:d={self.TRIM_FADE_SEC:.3f}"
            )
            self._log(
                f"   Сегмент {segment_index}: {self.MAX_ATEMPO_SPEED:.2f}x + trim {trim_amount:.2f}s "
                f"({current_duration:.2f}s → {target_duration_sec:.2f}s)"
            )

        try:
            cmd = [ffmpeg_path, "-i", audio_path, "-af", filter_chain, "-y", str(processed_path)]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

            if result.returncode != 0:
                logger.error(f"FFmpeg stderr seg {segment_index}: {result.stderr}")
                return audio_path

            if self._get_audio_duration(str(processed_path)) > 0:
                return str(processed_path)
            return audio_path

        except subprocess.TimeoutExpired:
            return audio_path
        except Exception as e:
            logger.error(f"Seg {segment_index} fit error: {e}", exc_info=True)
            return audio_path
    
    def _smart_trim(
        self,
        audio: AudioSegment,
        target_duration_ms: int,
        segment_index: int
    ) -> AudioSegment:
        """
        Обрезает аудио на границе тишины (между словами), а не посередине слова.

        Ищет последнюю паузу в окне SMART_TRIM_SEARCH_MS перед точкой обрезки.
        Если пауза не найдена — обрезает жёстко с fade-out (fallback).

        Args:
            audio: AudioSegment для обрезки
            target_duration_ms: Максимальная длительность (мс)
            segment_index: Индекс сегмента (для логов)

        Returns:
            Обрезанный AudioSegment
        """
        if len(audio) <= target_duration_ms:
            return audio

        overflow_ms = len(audio) - target_duration_ms

        # Ищем тишину в окне перед точкой обрезки
        search_start_ms = max(0, target_duration_ms - self.SMART_TRIM_SEARCH_MS)
        search_region = audio[search_start_ms:target_duration_ms]

        try:
            silences = detect_silence(
                search_region,
                min_silence_len=self.SMART_TRIM_MIN_SILENCE_MS,
                silence_thresh=self.SMART_TRIM_SILENCE_THRESH_DB,
                seek_step=10
            )
        except Exception:
            silences = []

        if silences:
            # Обрезаем на начале последней паузы (конец последнего слова)
            last_silence_start = silences[-1][0]
            trim_point_ms = search_start_ms + last_silence_start

            # Защита: trim point должен оставить >= 50% целевой длительности
            if trim_point_ms >= target_duration_ms * 0.5:
                trimmed = audio[:trim_point_ms]
                fade_ms = min(self.SEGMENT_FADE_MS, len(trimmed) // 4)
                if fade_ms > 0:
                    trimmed = trimmed.fade_out(fade_ms)
                self._log(
                    f"   Сегмент {segment_index}: smart trim на {trim_point_ms}мс "
                    f"(граница слова, сэкономлено {overflow_ms}мс)"
                )
                return trimmed

        # Fallback: жёсткая обрезка с fade-out
        trimmed = audio[:target_duration_ms]
        fade_ms = min(self.SEGMENT_FADE_MS, len(trimmed) // 4)
        if fade_ms > 0:
            trimmed = trimmed.fade_out(fade_ms)
        self._log(
            f"   Сегмент {segment_index}: hard trim на {target_duration_ms}мс "
            f"(пауза не найдена, overflow {overflow_ms}мс)"
        )
        return trimmed

    def _assemble_audio_timeline(
        self,
        segments: List[Dict],
        total_duration_sec: float,
        original_audio: Optional[AudioSegment] = None,
        background_volume_db: float = -18.0,
        vocals_path: Optional[str] = None,
        is_instruments: bool = False,
    ) -> AudioSegment:
        """
        Профессиональная сборка аудио-таймлайна (подход Linly-Dubbing).

        1. Adaptive Timing: пересчёт бюджетов с учётом фактических длительностей TTS
        2. Overlap prevention: каждый сегмент ограничен началом следующего
        3. Bi-directional time-stretch: 0.90x — 1.10x (pitch-preserving)
        4. Volume matching: TTS нормализуется к уровню оригинальных вокалов
        5. Чистый микс: TTS-дорожка (numpy) + инструменты (Demucs) = финал

        Args:
            segments: Сегменты с audio_file, start, end, actual_audio_duration
            total_duration_sec: Длительность видео
            original_audio: Фоновая дорожка (инструменты Demucs или приглушённый оригинал)
            background_volume_db: Громкость фоновой дорожки
            vocals_path: Путь к оригинальным вокалам (для volume matching)
            is_instruments: True если original_audio = чистые инструменты (Demucs)

        Returns:
            AudioSegment финального аудио
        """
        self._log(f"🎵 Сборка аудио ({len(segments)} сегментов, {total_duration_sec:.1f}s)...")

        # ── Adaptive Timing ──
        sorted_segments = compute_adaptive_durations(segments, total_duration_sec=total_duration_sec)

        pressure = estimate_timing_pressure(sorted_segments)
        if pressure['over_budget_count'] > 0:
            self._log(
                f"   Timing: {pressure['over_budget_count']} сегментов с дефицитом "
                f"({pressure['over_budget_total_sec']:.1f}s)"
            )

        extensions = [s.get('gap_extension', 0) for s in sorted_segments if s.get('gap_extension', 0) > 0]
        if extensions:
            self._log(
                f"   Elastic: {len(extensions)} расширены, "
                f"ср. +{sum(extensions)/len(extensions):.2f}s, макс +{max(extensions):.2f}s"
            )

        # ── Volume matching: измеряем уровень оригинальных вокалов ──
        vocal_peak = None
        if vocals_path and os.path.exists(vocals_path):
            try:
                vocal_audio = AudioSegment.from_file(vocals_path)
                vocal_peak = vocal_audio.max
                self._log(f"   Volume reference: vocals peak = {vocal_peak}")
            except Exception as e:
                logger.warning(f"Cannot load vocals for volume matching: {e}")

        total_duration_ms = int(total_duration_sec * 1000)
        sample_rate = 24000  # F5-TTS native sample rate

        # ── Собираем TTS-дорожку в numpy (подход Linly-Dubbing) ──
        tts_track = np.zeros(int(total_duration_sec * sample_rate), dtype=np.float32)

        processed_count = 0
        stretched_count = 0
        error_count = 0

        for i, seg in enumerate(sorted_segments):
            if self.should_stop_callback and self.should_stop_callback():
                self._log("Сборка прервана пользователем")
                raise InterruptedError("Processing stopped by user")

            audio_file = seg.get("audio_file")
            start = float(seg.get("start", 0))
            end = float(seg.get("end", start + 1.0))
            original_duration = end - start
            effective_duration = float(seg.get("effective_duration", original_duration))
            actual_dur = float(seg.get("actual_audio_duration", 0.0))

            if not audio_file or not os.path.exists(audio_file):
                error_count += 1
                continue

            # ── Overlap prevention (Linly-style) ──
            # Ограничиваем effective_duration началом следующего сегмента
            if i < len(sorted_segments) - 1:
                next_start = float(sorted_segments[i + 1].get("start", 0))
                max_end = next_start
                max_duration = max_end - start
                if max_duration > 0 and effective_duration > max_duration:
                    effective_duration = max_duration

            # ── Bi-directional time-stretch ──
            processed_audio_path = self._fit_audio_to_slot(
                audio_file, effective_duration, i,
                actual_audio_duration=actual_dur
            )

            if processed_audio_path != audio_file:
                stretched_count += 1

            try:
                segment_audio = AudioSegment.from_file(processed_audio_path)

                # Smart trim → Hard trim (гарантия)
                effective_duration_ms = int(effective_duration * 1000)
                if len(segment_audio) > effective_duration_ms:
                    segment_audio = self._smart_trim(segment_audio, effective_duration_ms, i)
                if len(segment_audio) > effective_duration_ms:
                    segment_audio = segment_audio[:effective_duration_ms]

                # Fade in/out (устранение щелчков)
                fade_ms = self.SEGMENT_FADE_MS
                if len(segment_audio) > fade_ms * 3:
                    segment_audio = segment_audio.fade_in(fade_ms).fade_out(fade_ms)

                # Конвертируем в numpy float32
                seg_samples = np.array(segment_audio.get_array_of_samples(), dtype=np.float32)
                if segment_audio.channels == 2:
                    # Mono mix
                    seg_samples = seg_samples.reshape(-1, 2).mean(axis=1)
                # Нормализуем к [-1, 1]
                if segment_audio.sample_width == 2:
                    seg_samples = seg_samples / 32768.0
                elif segment_audio.sample_width == 4:
                    seg_samples = seg_samples / 2147483648.0

                # Ресемплируем если нужно (segment может быть не 24kHz)
                if segment_audio.frame_rate != sample_rate:
                    # Простой ресемплинг через соотношение длины
                    target_len = int(len(seg_samples) * sample_rate / segment_audio.frame_rate)
                    seg_samples = np.interp(
                        np.linspace(0, len(seg_samples) - 1, target_len),
                        np.arange(len(seg_samples)),
                        seg_samples
                    ).astype(np.float32)

                # ── Размещение в TTS-дорожке по позиции ──
                start_sample = int(start * sample_rate)
                end_sample = start_sample + len(seg_samples)

                # Защита от выхода за пределы
                if end_sample > len(tts_track):
                    seg_samples = seg_samples[:len(tts_track) - start_sample]
                    end_sample = len(tts_track)

                if start_sample < len(tts_track) and len(seg_samples) > 0:
                    tts_track[start_sample:start_sample + len(seg_samples)] = seg_samples
                    processed_count += 1

            except InterruptedError:
                raise
            except Exception as e:
                self._log(f"   Сегмент {i}: ошибка — {e}")
                logger.error(f"Seg {i} assembly error", exc_info=True)
                error_count += 1
                continue

        # ── Volume matching к оригинальным вокалам (Linly-подход) ──
        tts_peak = np.max(np.abs(tts_track))
        if tts_peak > 0:
            if vocal_peak and vocal_peak > 0:
                # Нормализуем TTS к уровню оригинальных вокалов
                vocal_peak_float = vocal_peak / 32768.0  # pydub .max = int16 peak
                scale = vocal_peak_float / tts_peak
                tts_track *= scale
                self._log(f"   Volume matched: TTS peak → vocal level (scale={scale:.2f})")
            else:
                # Без вокалов — просто нормализуем до -3dB headroom
                target_peak = 10 ** (-3.0 / 20.0)  # -3dB ≈ 0.708
                tts_track *= (target_peak / tts_peak)

        # ── Микс TTS + фоновая дорожка ──
        if original_audio is not None:
            bg = original_audio[:total_duration_ms]
            if len(bg) < total_duration_ms:
                bg = bg + AudioSegment.silent(duration=total_duration_ms - len(bg))

            # Конвертируем фон в numpy mono float32
            bg_samples = np.array(bg.get_array_of_samples(), dtype=np.float32)
            if bg.channels == 2:
                bg_samples = bg_samples.reshape(-1, 2).mean(axis=1)
            if bg.sample_width == 2:
                bg_samples = bg_samples / 32768.0
            elif bg.sample_width == 4:
                bg_samples = bg_samples / 2147483648.0

            # Ресемплируем фон к sample_rate TTS
            if bg.frame_rate != sample_rate:
                target_len = int(len(bg_samples) * sample_rate / bg.frame_rate)
                bg_samples = np.interp(
                    np.linspace(0, len(bg_samples) - 1, target_len),
                    np.arange(len(bg_samples)),
                    bg_samples
                ).astype(np.float32)

            # Выравниваем длины
            min_len = min(len(tts_track), len(bg_samples))
            tts_track = tts_track[:min_len]
            bg_samples = bg_samples[:min_len]

            # Применяем громкость к фону
            bg_gain = 10 ** (background_volume_db / 20.0)
            bg_samples *= bg_gain

            if is_instruments:
                # Demucs инструменты: прямое сложение (нет конфликта голосов)
                combined = tts_track + bg_samples
                self._log(f"   Микс: TTS + инструменты (Demucs, {background_volume_db}dB)")
            else:
                # Оригинальное аудио: сложение (оригинал приглушён)
                combined = tts_track + bg_samples
                self._log(f"   Микс: TTS + оригинал ({background_volume_db}dB)")
        else:
            combined = tts_track
            self._log(f"   Без фоновой дорожки")

        # ── Финальный клиппинг и конвертация ──
        peak = np.max(np.abs(combined))
        if peak > 1.0:
            combined = combined / peak * 0.95  # Soft limiter
            self._log(f"   Limiter: peak {peak:.2f} → 0.95")

        # Конвертируем обратно в int16
        combined_int16 = (combined * 32767).astype(np.int16)

        # Создаём AudioSegment из numpy
        result = AudioSegment(
            data=combined_int16.tobytes(),
            sample_width=2,
            frame_rate=sample_rate,
            channels=1,
        )

        # Дополняем до нужной длительности (если tts_track короче total_duration)
        if len(result) < total_duration_ms:
            result = result + AudioSegment.silent(duration=total_duration_ms - len(result))

        self._log(
            f"✅ Таймлайн: {processed_count}/{len(sorted_segments)} сегментов "
            f"({stretched_count} растянуты, {error_count} ошибок)"
        )

        return result
    
    def make_video(
        self,
        video_path: str,
        segments: List[Dict],
        output_path: str,
        background_volume_db: float = -18.0,
        instruments_path: Optional[str] = None,
        vocals_path: Optional[str] = None,
    ) -> str:
        """
        Создает финальное дублированное видео.

        Args:
            video_path: Путь к оригинальному видео
            segments: Список сегментов с audio_file, start, end
            output_path: Путь для сохранения результата
            background_volume_db: Громкость фоновой дорожки оригинала в dB
                                  (-18 по умолчанию, None = без фона)
            instruments_path: Путь к разделённой инструментальной дорожке (Demucs).
                              Если указан — используется вместо приглушённого оригинала.
            vocals_path: Путь к оригинальным вокалам (Demucs) для volume matching.

        Returns:
            Путь к созданному видео файлу
        """
        # На Windows длинные/кириллические пути требуют \\?\ или короткого пути 8.3
        video_path_resolved = resolve_path_for_win(video_path)
        output_path_resolved = resolve_path_for_win(output_path)
        if not os.path.exists(video_path_resolved):
            raise FileNotFoundError(f"Видео файл не найден: {video_path}")
        
        if not segments:
            raise ValueError("Нет сегментов для обработки")
        
        # Проверяем и устанавливаем MoviePy при необходимости
        if not MOVIEPY_AVAILABLE:
            self._log("📦 MoviePy не найден, пытаемся установить...")
            if _install_moviepy():
                self._log("✅ MoviePy успешно установлен, продолжаем...")
            else:
                raise ImportError(
                    "MoviePy не установлен. Установите: pip install moviepy\n"
                    "Или используйте альтернативный метод сборки видео."
                )
        
        self._log(f"\n🎬 СОЗДАНИЕ ДУБЛИРОВАННОГО ВИДЕО")
        self._log("─" * 50)
        self._log(f"📹 Исходное видео: {os.path.basename(video_path_resolved)}")
        self._log(f"📊 Сегментов: {len(segments)}")
        
        try:
            # ШАГ 1: Загружаем оригинальное видео для получения длительности
            self._log(f"\n📹 Шаг 1/4: Загрузка оригинального видео...")
            try:
                # Пытаемся импортировать VideoFileClip и AudioFileClip
                try:
                    from moviepy import VideoFileClip, AudioFileClip
                except ImportError:
                    from moviepy.editor import VideoFileClip, AudioFileClip
            except ImportError as e:
                # Если импорт не удался, пытаемся установить и повторить
                self._log("📦 MoviePy не найден при использовании, пытаемся установить...")
                if _install_moviepy():
                    # Повторяем импорт после установки
                    try:
                        from moviepy import VideoFileClip, AudioFileClip
                    except ImportError:
                        from moviepy.editor import VideoFileClip, AudioFileClip
                    self._log("✅ MoviePy установлен, продолжаем...")
                else:
                    raise ImportError(
                        f"MoviePy не установлен после попытки установки. "
                        f"Установите вручную: pip install moviepy"
                    )
            
            video_clip = VideoFileClip(video_path_resolved)
            total_duration = video_clip.duration
            self._log(f"✅ Длительность видео: {total_duration:.1f} секунд")

            # Извлекаем фоновую дорожку
            original_audio = None
            use_instruments = False

            if instruments_path and os.path.exists(instruments_path):
                # Demucs: чистые инструменты (без речи) — профессиональный микс
                try:
                    self._log(f"🎸 Загрузка инструментов (Demucs)...")
                    original_audio = AudioSegment.from_file(instruments_path)
                    use_instruments = True
                    # Инструменты без речи — прямое сложение с TTS
                    background_volume_db = -3.0
                    self._log(f"✅ Инструменты: {len(original_audio)/1000:.1f}s")
                except Exception as audio_err:
                    self._log(f"⚠️ Не удалось загрузить инструменты: {audio_err}")
                    original_audio = None

            if original_audio is None and background_volume_db is not None:
                try:
                    self._log(f"🔊 Извлечение оригинального аудио (fallback)...")
                    original_audio = AudioSegment.from_file(video_path_resolved)
                    self._log(f"✅ Оригинальное аудио: {len(original_audio)/1000:.1f}s")
                except Exception as audio_err:
                    self._log(f"⚠️ Не удалось извлечь оригинальное аудио: {audio_err}")
                    original_audio = None

            # ШАГ 2: Собираем аудио временную линию
            self._log(f"\n🎵 Шаг 2/4: Сборка аудио временной линии...")
            assembled_audio = self._assemble_audio_timeline(
                segments, total_duration,
                original_audio=original_audio,
                background_volume_db=background_volume_db if background_volume_db is not None else -18.0,
                vocals_path=vocals_path,
                is_instruments=use_instruments,
            )
            
            # Сохраняем собранное аудио во временный файл
            temp_audio_path = self.temp_dir / "assembled_audio.wav"
            assembled_audio.export(str(temp_audio_path), format="wav")
            self._log(f"✅ Аудио сохранено: {temp_audio_path}")
            
            # ШАГ 3: Заменяем аудио дорожку в видео
            self._log(f"\n🔗 Шаг 3/4: Замена аудио дорожки...")
            audio_clip = AudioFileClip(str(temp_audio_path))
            
            # Обрезаем аудио до длительности видео (если нужно)
            if audio_clip.duration > total_duration:
                audio_clip = audio_clip.subclip(0, total_duration)
                self._log(f"   Аудио обрезано до {total_duration:.1f}s")
            elif audio_clip.duration < total_duration:
                # Если аудио короче, просто используем как есть
                self._log(f"   Аудио короче видео на {total_duration - audio_clip.duration:.1f}s")
            
            # Заменяем аудио (MoviePy 2.x использует with_audio вместо set_audio)
            final_video = video_clip.with_audio(audio_clip)
            
            # ШАГ 4: Экспортируем финальное видео
            self._log(f"\n💾 Шаг 4/4: Экспорт финального видео...")
            output_path_obj = Path(output_path_resolved)
            output_path_obj.parent.mkdir(parents=True, exist_ok=True)
            
            # Экспортируем с настройками качества
            # MoviePy 2.x: write_videofile параметры
            final_video.write_videofile(
                str(output_path_resolved),
                codec='libx264',
                audio_codec='aac',
                temp_audiofile=str(self.temp_dir / "temp_audio.m4a"),
                remove_temp=True,
                logger=None  # Отключаем прогресс-бар (logger='bar' по умолчанию)
            )
            
            # Закрываем клипы для освобождения ресурсов
            audio_clip.close()
            video_clip.close()
            final_video.close()
            
            # Удаляем временное аудио
            if temp_audio_path.exists():
                temp_audio_path.unlink()
            
            self._log(f"\n✅ ДУБЛИРОВАННОЕ ВИДЕО СОЗДАНО!")
            self._log(f"📄 Файл: {output_path_resolved}")
            
            return str(output_path_resolved)
            
        except ImportError as e:
            error_msg = str(e)
            # Если ошибка связана с MoviePy - пытаемся установить и повторить
            if "moviepy" in error_msg.lower() or "MoviePy" in error_msg:
                self._log("📦 Обнаружена ошибка MoviePy, пытаемся установить...")
                if _install_moviepy():
                    self._log("✅ MoviePy установлен, повторяем попытку создания видео...")
                    # Повторяем весь процесс
                    return self.make_video(video_path_resolved, segments, output_path_resolved, background_volume_db)
                else:
                    self._log(f"❌ Не удалось установить MoviePy: {error_msg}")
                    raise ImportError(
                        f"MoviePy не установлен после попытки установки. "
                        f"Установите вручную: pip install moviepy"
                    )
            else:
                raise
        except Exception as e:
            self._log(f"❌ Ошибка создания видео: {str(e)}")
            import traceback
            self._log(f"📋 Детали: {traceback.format_exc()}")
            raise
    
    def cleanup_temp_files(self):
        """Очищает временные файлы"""
        try:
            if self.processed_audio_dir.exists():
                for file in self.processed_audio_dir.glob("*.wav"):
                    file.unlink()
                self._log(f"🧹 Временные файлы очищены")
        except Exception as e:
            self._log(f"⚠️ Ошибка очистки: {e}")

# -*- coding: utf-8 -*-
"""
Video Maker Module
Сборка финального дублированного видео из сегментов с TTS аудио.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Dict, Optional, Callable
from pydub import AudioSegment
from pydub.silence import detect_silence
import logging
from core.config import APP_PATHS, resolve_path_for_win
from core.elastic_timing import compute_effective_durations

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
    Класс для сборки финального дублированного видео.

    Обрабатывает:
    - Минимальное ускорение TTS аудио (только до 1.10x, если необходимо)
    - НИКОГДА не замедляет аудио (это ухудшает качество)
    - Сборку временной линии из всех сегментов
    - Замену аудио дорожки в оригинальном видео
    """

    # Профессиональные ограничения для качества дубляжа
    MAX_ATEMPO_SPEED = 1.10  # Максимальное ускорение atempo (минимальное влияние на качество)
    MIN_ATEMPO_SPEED = 1.0   # Никогда не замедляем!
    SEGMENT_FADE_MS = 50     # Fade in/out для каждого сегмента после подгонки (устраняет щелчки на стыках)
    TRIM_FADE_SEC = 0.050    # Fade-out после обрезки в FFmpeg (секунды)
    SMART_TRIM_SEARCH_MS = 500      # Окно поиска тишины перед точкой обрезки (мс)
    SMART_TRIM_MIN_SILENCE_MS = 80  # Минимальная длина тишины для trim-точки (мс)
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
        segment_index: int
    ) -> str:
        """
        Подгоняет аудио к заданной длительности используя FFmpeg atempo фильтр.

        ПРОФЕССИОНАЛЬНЫЕ ПРАВИЛА:
        - Никогда не замедляем аудио (ухудшает качество)
        - Ускоряем максимум до 1.10x (минимальное влияние на качество)
        - Если аудио слишком длинное — обрезаем конец (лучше чем сильное ускорение)

        Args:
            audio_path: Путь к исходному аудио файлу
            target_duration_sec: Целевая длительность в секундах
            segment_index: Индекс сегмента (для имени временного файла)

        Returns:
            Путь к обработанному аудио файлу (или исходному, если изменение не требуется)
        """
        if not os.path.exists(audio_path):
            self._log(f"❌ Файл не найден: {audio_path}")
            return audio_path

        # Получаем текущую длительность
        current_duration = self._get_audio_duration(audio_path)

        if current_duration <= 0:
            self._log(f"⚠️ Не удалось определить длительность {audio_path}")
            return audio_path

        # Вычисляем фактор скорости
        speed_factor = current_duration / target_duration_sec

        # Если аудио уже короче или равно целевой длительности - не изменяем
        if speed_factor <= 1.0:
            self._log(f"   Сегмент {segment_index}: ✓ аудио подходит ({current_duration:.2f}s ≤ {target_duration_sec:.2f}s)")
            return audio_path

        # Проверяем наличие FFmpeg
        ffmpeg_path = self._get_ffmpeg_path()
        if not ffmpeg_path:
            self._log(f"❌ FFmpeg не найден! Невозможно обработать аудио.")
            return audio_path

        # Создаем путь для обработанного файла
        processed_path = self.processed_audio_dir / f"segment_{segment_index:04d}_processed.wav"

        # Профессиональный подход: ограничиваем ускорение до MAX_ATEMPO_SPEED
        effective_speed = min(speed_factor, self.MAX_ATEMPO_SPEED)

        # Если требуется слишком сильное ускорение — сначала ускоряем на MAX, потом обрежем
        need_trim = speed_factor > self.MAX_ATEMPO_SPEED

        if need_trim:
            # Ускоряем на максимум, потом обрежем лишнее
            accelerated_duration = current_duration / self.MAX_ATEMPO_SPEED
            trim_amount = accelerated_duration - target_duration_sec
            self._log(f"   Сегмент {segment_index}: ⚠️ слишком длинный! Ускорение {self.MAX_ATEMPO_SPEED:.2f}x + обрезка {trim_amount:.2f}s")
        else:
            self._log(f"   Сегмент {segment_index}: ускорение {current_duration:.2f}s → {target_duration_sec:.2f}s ({effective_speed:.2f}x)")

        try:
            # Формируем фильтр atempo (работает в диапазоне 0.5-2.0, наш MAX <= 1.15)
            filter_chain = f"atempo={effective_speed:.3f}"

            # Если нужна обрезка — добавляем trim фильтр + fade-out для плавного окончания
            if need_trim:
                fade_start = max(0, target_duration_sec - self.TRIM_FADE_SEC)
                filter_chain = (
                    f"atempo={self.MAX_ATEMPO_SPEED:.3f},"
                    f"atrim=0:{target_duration_sec:.3f},"
                    f"afade=t=out:st={fade_start:.3f}:d={self.TRIM_FADE_SEC:.3f}"
                )

            # Запускаем FFmpeg
            cmd = [
                ffmpeg_path,
                "-i", audio_path,
                "-af", filter_chain,
                "-y",
                str(processed_path)
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60
            )

            if result.returncode != 0:
                self._log(f"❌ Ошибка FFmpeg: {result.stderr}")
                return audio_path

            # Проверяем результат
            processed_duration = self._get_audio_duration(str(processed_path))
            if processed_duration > 0:
                status = "✅" if processed_duration <= target_duration_sec * 1.05 else "⚠️"
                self._log(f"   {status} Результат: {processed_duration:.2f}s (цель: {target_duration_sec:.2f}s)")
                return str(processed_path)
            else:
                self._log(f"⚠️ Обработанный файл пуст, используем оригинал")
                return audio_path

        except subprocess.TimeoutExpired:
            self._log(f"❌ Таймаут обработки аудио")
            return audio_path
        except Exception as e:
            self._log(f"❌ Ошибка обработки аудио: {e}")
            import traceback
            self._log(f"📋 Детали: {traceback.format_exc()}")
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
        background_volume_db: float = -18.0
    ) -> AudioSegment:
        """
        Собирает временную линию аудио из всех сегментов.

        Использует Elastic Timing: каждый сегмент может расширяться в паузу
        после него, как это делает профессиональный дублёр-синхронист.

        Если передан original_audio — используется как приглушённая фоновая
        дорожка (профессиональный дубляж: оригинал слышен на фоне).

        Args:
            segments: Список сегментов с audio_file и timestamps
            total_duration_sec: Общая длительность видео в секундах
            original_audio: Оригинальное аудио из видео (или None)
            background_volume_db: Громкость фоновой дорожки в dB (по умолчанию -18)

        Returns:
            AudioSegment с собранным аудио
        """
        self._log(f"🎵 Сборка аудио временной линии ({len(segments)} сегментов, {total_duration_sec:.1f}s)...")

        # --- Elastic Timing: вычисляем эффективные длительности ---
        sorted_segments = compute_effective_durations(segments, total_duration_sec=total_duration_sec)

        extensions = [s.get('gap_extension', 0) for s in sorted_segments if s.get('gap_extension', 0) > 0]
        if extensions:
            self._log(
                f"   Elastic timing: {len(extensions)} сегментов расширены, "
                f"среднее +{sum(extensions)/len(extensions):.2f}s, макс +{max(extensions):.2f}s"
            )

        total_duration_ms = int(total_duration_sec * 1000)

        # --- Фоновая дорожка: приглушённый оригинал ---
        if original_audio is not None:
            bg = original_audio[:total_duration_ms]
            # Дополняем тишиной если оригинал короче видео
            if len(bg) < total_duration_ms:
                bg = bg + AudioSegment.silent(duration=total_duration_ms - len(bg))
            canvas = bg.apply_gain(background_volume_db)
            self._log(f"   Фоновая дорожка: оригинал приглушён до {background_volume_db}dB")
        else:
            canvas = AudioSegment.silent(duration=total_duration_ms)

        processed_count = 0
        error_count = 0

        for i, seg in enumerate(sorted_segments):
            # Проверяем флаг остановки
            if self.should_stop_callback and self.should_stop_callback():
                self._log("⏹️ Сборка видео прервана пользователем")
                raise InterruptedError("Processing stopped by user")

            audio_file = seg.get("audio_file")
            start = float(seg.get("start", 0))
            end = float(seg.get("end", start + 1.0))
            original_duration = end - start
            effective_duration = float(seg.get("effective_duration", original_duration))
            gap_ext = float(seg.get("gap_extension", 0.0))

            if not audio_file or not os.path.exists(audio_file):
                self._log(f"⚠️ Сегмент {i}: аудио файл отсутствует, пропускаем")
                error_count += 1
                continue

            # Подгоняем аудио к ЭФФЕКТИВНОМУ слоту (с учётом паузы)
            processed_audio_path = self._fit_audio_to_slot(
                audio_file,
                effective_duration,
                i
            )

            try:
                segment_audio = AudioSegment.from_file(processed_audio_path)

                # Smart trim: если аудио всё ещё длиннее эффективного слота —
                # обрезаем на границе тишины (между словами), а не посередине
                effective_duration_ms = int(effective_duration * 1000)
                if len(segment_audio) > effective_duration_ms:
                    segment_audio = self._smart_trim(segment_audio, effective_duration_ms, i)

                # Страховка: гарантируем что не выходим за эффективную длительность
                if len(segment_audio) > effective_duration_ms:
                    segment_audio = segment_audio[:effective_duration_ms]

                # Fade in/out ПОСЛЕ всех обрезок — устраняет щелчки на стыках
                fade_ms = self.SEGMENT_FADE_MS
                if len(segment_audio) > fade_ms * 3:
                    segment_audio = segment_audio.fade_in(fade_ms).fade_out(fade_ms)

                # Накладываем на холст в ОРИГИНАЛЬНОЙ позиции
                # Аудио естественно заходит в паузу после сегмента
                start_ms = int(start * 1000)
                canvas = canvas.overlay(segment_audio, position=start_ms)

                processed_count += 1

                if gap_ext > 0:
                    self._log(
                        f"   Сегмент {i}: {len(segment_audio)/1000:.2f}s "
                        f"(elastic: +{gap_ext:.2f}s из паузы)"
                    )

            except InterruptedError:
                self._log("⏹️ Сборка видео прервана пользователем")
                raise
            except Exception as e:
                self._log(f"❌ Ошибка обработки сегмента {i}: {e}")
                error_count += 1
                continue

        self._log(
            f"✅ Временная линия собрана: {processed_count}/{len(sorted_segments)} "
            f"сегментов, {error_count} ошибок"
        )

        return canvas
    
    def make_video(
        self,
        video_path: str,
        segments: List[Dict],
        output_path: str,
        background_volume_db: float = -18.0,
        instruments_path: Optional[str] = None
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
            if instruments_path and os.path.exists(instruments_path):
                # Используем чистые инструменты из Demucs (без речи)
                try:
                    self._log(f"🎸 Загрузка инструментальной дорожки (Demucs)...")
                    original_audio = AudioSegment.from_file(instruments_path)
                    # Инструменты без речи — можно громче чем -18дБ
                    background_volume_db = -6.0
                    self._log(f"✅ Инструменты: {len(original_audio)/1000:.1f}s (громкость: {background_volume_db}дБ)")
                except Exception as audio_err:
                    self._log(f"⚠️ Не удалось загрузить инструменты: {audio_err}")
                    original_audio = None

            if original_audio is None and background_volume_db is not None:
                try:
                    self._log(f"🔊 Извлечение оригинального аудио для фоновой дорожки...")
                    original_audio = AudioSegment.from_file(video_path_resolved)
                    self._log(f"✅ Оригинальное аудио: {len(original_audio)/1000:.1f}s")
                except Exception as audio_err:
                    self._log(f"⚠️ Не удалось извлечь оригинальное аудио: {audio_err}")
                    self._log(f"   Продолжаем без фоновой дорожки")
                    original_audio = None

            # ШАГ 2: Собираем аудио временную линию
            self._log(f"\n🎵 Шаг 2/4: Сборка аудио временной линии...")
            assembled_audio = self._assemble_audio_timeline(
                segments, total_duration,
                original_audio=original_audio,
                background_volume_db=background_volume_db if background_volume_db is not None else -18.0
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

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
import logging

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
    - Сжатие TTS аудио до нужной длительности (atempo фильтр)
    - Сборку временной линии из всех сегментов
    - Замену аудио дорожки в оригинальном видео
    """
    
    def __init__(
        self,
        temp_dir: str = "temp",
        progress_callback: Optional[Callable[[str], None]] = None,
        should_stop_callback: Optional[Callable[[], bool]] = None
    ):
        """
        Инициализация VideoMaker.
        
        Args:
            temp_dir: Директория для временных файлов
            progress_callback: Функция для логирования прогресса
        """
        self.temp_dir = Path(temp_dir)
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
            self._log(f"   Сегмент {segment_index}: аудио уже подходит ({current_duration:.2f}s <= {target_duration_sec:.2f}s)")
            return audio_path
        
        self._log(f"   Сегмент {segment_index}: сжатие {current_duration:.2f}s → {target_duration_sec:.2f}s (фактор: {speed_factor:.2f}x)")
        
        # Проверяем наличие FFmpeg
        ffmpeg_path = self._get_ffmpeg_path()
        if not ffmpeg_path:
            self._log(f"❌ FFmpeg не найден! Невозможно сжать аудио.")
            return audio_path
        
        # Создаем цепочку atempo фильтров
        # atempo работает в диапазоне 0.5-2.0
        atempo_filters = []
        remaining_factor = speed_factor
        
        while remaining_factor > 1.0:
            if remaining_factor <= 2.0:
                # Один фильтр достаточен
                atempo_filters.append(f"atempo={remaining_factor:.3f}")
                break
            else:
                # Нужна цепочка: применяем максимальный фактор 2.0
                atempo_filters.append("atempo=2.0")
                remaining_factor = remaining_factor / 2.0
        
        # Если остался фактор < 1.0, это ошибка (не должно быть)
        if remaining_factor < 1.0:
            self._log(f"⚠️ Ошибка вычисления фактора: {remaining_factor}")
            return audio_path
        
        # Объединяем фильтры
        filter_chain = ",".join(atempo_filters)
        
        # Создаем путь для обработанного файла
        processed_path = self.processed_audio_dir / f"segment_{segment_index:04d}_processed.wav"
        
        try:
            # Запускаем FFmpeg для сжатия аудио
            cmd = [
                ffmpeg_path,
                "-i", audio_path,
                "-af", filter_chain,
                "-y",  # Перезаписать выходной файл
                str(processed_path)
            ]
            
            self._log(f"   🎚️ FFmpeg команда: {' '.join(cmd)}")
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60  # Максимум 60 секунд на обработку
            )
            
            if result.returncode != 0:
                self._log(f"❌ Ошибка FFmpeg: {result.stderr}")
                return audio_path
            
            # Проверяем результат
            processed_duration = self._get_audio_duration(str(processed_path))
            if processed_duration > 0:
                self._log(f"   ✅ Обработано: {processed_duration:.2f}s (цель: {target_duration_sec:.2f}s)")
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
    
    def _assemble_audio_timeline(
        self,
        segments: List[Dict],
        total_duration_sec: float
    ) -> AudioSegment:
        """
        Собирает временную линию аудио из всех сегментов.
        
        Args:
            segments: Список сегментов с audio_file и timestamps
            total_duration_sec: Общая длительность видео в секундах
            
        Returns:
            AudioSegment с собранным аудио
        """
        self._log(f"🎵 Сборка аудио временной линии ({len(segments)} сегментов, общая длительность: {total_duration_sec:.1f}s)...")
        
        # Создаем "холст" - тихое аудио нужной длительности
        total_duration_ms = int(total_duration_sec * 1000)
        canvas = AudioSegment.silent(duration=total_duration_ms)
        
        processed_count = 0
        error_count = 0
        
        for i, seg in enumerate(segments):
            # Проверяем флаг остановки в цикле
            if self.should_stop_callback and self.should_stop_callback():
                self._log("⏹️ Сборка видео прервана пользователем")
                raise InterruptedError("Processing stopped by user")
            
            audio_file = seg.get("audio_file")
            start = float(seg.get("start", 0))
            end = float(seg.get("end", start + 1.0))
            
            if not audio_file or not os.path.exists(audio_file):
                self._log(f"⚠️ Сегмент {i}: аудио файл отсутствует, пропускаем")
                error_count += 1
                continue
            
            # Вычисляем целевую длительность для этого сегмента
            target_duration = end - start
            
            # Подгоняем аудио к слоту
            processed_audio_path = self._fit_audio_to_slot(
                audio_file,
                target_duration,
                i
            )
            
            try:
                # Загружаем обработанное аудио
                segment_audio = AudioSegment.from_file(processed_audio_path)
                
                # Обрезаем до целевой длительности (на случай, если все еще длиннее)
                if len(segment_audio) > target_duration * 1000:
                    segment_audio = segment_audio[:int(target_duration * 1000)]
                    self._log(f"   Сегмент {i}: обрезано до {target_duration:.2f}s")
                
                # Накладываем на холст в нужной позиции
                start_ms = int(start * 1000)
                canvas = canvas.overlay(segment_audio, position=start_ms)
                
                processed_count += 1
                
            except InterruptedError:
                self._log("⏹️ Сборка видео прервана пользователем")
                raise
            except Exception as e:
                self._log(f"❌ Ошибка обработки сегмента {i}: {e}")
                error_count += 1
                continue
        
        self._log(f"✅ Временная линия собрана: {processed_count}/{len(segments)} сегментов обработано, {error_count} ошибок")
        
        return canvas
    
    def make_video(
        self,
        video_path: str,
        segments: List[Dict],
        output_path: str
    ) -> str:
        """
        Создает финальное дублированное видео.
        
        Args:
            video_path: Путь к оригинальному видео
            segments: Список сегментов с audio_file, start, end
            output_path: Путь для сохранения результата
            
        Returns:
            Путь к созданному видео файлу
        """
        if not os.path.exists(video_path):
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
        self._log(f"📹 Исходное видео: {os.path.basename(video_path)}")
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
            
            video_clip = VideoFileClip(video_path)
            total_duration = video_clip.duration
            self._log(f"✅ Длительность видео: {total_duration:.1f} секунд")
            
            # ШАГ 2: Собираем аудио временную линию
            self._log(f"\n🎵 Шаг 2/4: Сборка аудио временной линии...")
            assembled_audio = self._assemble_audio_timeline(segments, total_duration)
            
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
            output_path_obj = Path(output_path)
            output_path_obj.parent.mkdir(parents=True, exist_ok=True)
            
            # Экспортируем с настройками качества
            # MoviePy 2.x: write_videofile параметры
            final_video.write_videofile(
                str(output_path),
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
            self._log(f"📄 Файл: {output_path}")
            
            return str(output_path)
            
        except ImportError as e:
            error_msg = str(e)
            # Если ошибка связана с MoviePy - пытаемся установить и повторить
            if "moviepy" in error_msg.lower() or "MoviePy" in error_msg:
                self._log("📦 Обнаружена ошибка MoviePy, пытаемся установить...")
                if _install_moviepy():
                    self._log("✅ MoviePy установлен, повторяем попытку создания видео...")
                    # Повторяем весь процесс
                    return self.make_video(video_path, segments, output_path)
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

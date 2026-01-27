# -*- coding: utf-8 -*-
"""
Voice Cloning Module using Coqui XTTS v2
Генерация дубляжа с клонированием голоса спикеров
"""
import os
import logging
import subprocess
import json
import sys
import time
from pathlib import Path
from typing import List, Dict, Optional, Callable
import torch
from pydub import AudioSegment

# Пытаемся импортировать TTS (поддерживаем и старый TTS, и новый coqui-tts)
TTS_AVAILABLE = False
TTS = None
TTS_ERROR = None

try:
    # Пробуем импортировать TTS API
    from TTS.api import TTS
    TTS_AVAILABLE = True
except ImportError as e:
    # TTS не установлен
    TTS_AVAILABLE = False
    TTS = None
    TTS_ERROR = f"ImportError: {str(e)}"
except (TypeError, SyntaxError) as e:
    # Ошибки совместимости Python версии (обычно Python < 3.10)
    TTS_AVAILABLE = False
    TTS = None
    TTS_ERROR = f"CompatibilityError: {str(e)}"
except Exception as e:
    # Другие неожиданные ошибки
    TTS_AVAILABLE = False
    TTS = None
    TTS_ERROR = f"UnexpectedError: {str(e)}"


class VoiceCloner:
    """
    Класс для клонирования голоса и генерации дубляжа с использованием Coqui XTTS v2.
    """
    
    def __init__(
        self,
        model_name: str = "tts_models/multilingual/multi-dataset/xtts_v2",
        progress_callback: Optional[Callable[[str], None]] = None,
        should_stop_callback: Optional[Callable[[], bool]] = None
    ):
        """
        Инициализация VoiceCloner.
        
        Args:
            model_name: Название модели XTTS (по умолчанию xtts_v2)
            progress_callback: Функция для логирования прогресса
        """
        # Подавляем промпт лицензии
        os.environ["COQUI_TOS_AGREED"] = "1"
        
        self.model_name = model_name
        self.progress_callback = progress_callback
        self.should_stop_callback = should_stop_callback
        self.model = None
        
        # Определяем устройство
        self.device = self._detect_device()
        
        # Создаем необходимые директории
        self.voices_dir = Path("voices")
        self.voices_dir.mkdir(exist_ok=True)
        
        self.temp_tts_dir = Path("temp/tts_parts")
        self.temp_tts_dir.mkdir(parents=True, exist_ok=True)
        
        # Проверяем наличие venv_tts для использования через subprocess
        self.venv_tts_path = self._find_venv_tts()
        self.use_venv_tts = self.venv_tts_path is not None and not TTS_AVAILABLE
        
        if self.use_venv_tts:
            self._log(f"🎤 VoiceCloner инициализирован (устройство: {self.device}, используется venv_tts)")
        else:
            self._log(f"🎤 VoiceCloner инициализирован (устройство: {self.device})")
    
    def _log(self, msg: str):
        """Логирование в UI и консоль"""
        print(msg)
        if self.progress_callback:
            self.progress_callback(msg)
    
    def _detect_device(self) -> str:
        """
        Определяет устройство для TTS.
        Для Mac (Apple Silicon) используем CPU по умолчанию для стабильности.
        """
        if torch.cuda.is_available():
            return "cuda"
        
        # Для Mac проверяем MPS, но по умолчанию используем CPU для стабильности
        if hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
            # MPS доступен, но XTTS может быть нестабилен на MPS
            # Используем CPU для надежности на Mac
            return "cpu"
        
        return "cpu"
    
    def _find_venv_tts(self) -> Optional[Path]:
        """
        Ищет venv_tts в текущей директории или родительских.
        """
        current = Path.cwd()
        
        # Проверяем текущую директорию
        venv_path = current / "venv_tts" / "bin" / "python3"
        if venv_path.exists():
            return venv_path
        
        # Проверяем родительские директории (до 3 уровней вверх)
        for i in range(3):
            parent = current.parent if i > 0 else current
            venv_path = parent / "venv_tts" / "bin" / "python3"
            if venv_path.exists():
                return venv_path
        
        return None
    
    def _load_model(self):
        """Ленивая загрузка модели XTTS"""
        if self.model is not None:
            return
        
        # Если используем venv_tts через subprocess, модель не загружаем
        if self.use_venv_tts:
            self._log(f"✅ Используется venv_tts для генерации TTS (Python 3.11+)")
            return
        
        if not TTS_AVAILABLE:
            import sys
            python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
            
            error_msg = (
                f"❌ TTS (Coqui TTS) недоступен.\n"
                f"   Текущая версия Python: {python_version}\n"
                f"   Требуется: Python 3.10+\n\n"
            )
            
            if TTS_ERROR:
                error_msg += f"   Ошибка: {TTS_ERROR}\n\n"
            
            error_msg += (
                "💡 Автоматическая установка:\n"
                "   Запустите: ./setup_voice_cloning.sh\n\n"
                "   Или вручную:\n"
                "   1. brew install python@3.11\n"
                "   2. python3.11 -m venv venv_tts\n"
                "   3. source venv_tts/bin/activate\n"
                "   4. pip install coqui-tts pydub\n"
            )
            
            raise ImportError(error_msg)
        
        self._log(f"📦 Загрузка модели XTTS: {self.model_name}...")
        try:
            self.model = TTS(model_name=self.model_name, progress_bar=False)
            self.model.to(self.device)
            self._log(f"✅ Модель XTTS загружена на {self.device}")
        except Exception as e:
            self._log(f"❌ Ошибка загрузки модели XTTS: {e}")
            raise
    
    def _generate_tts_via_venv(self, text: str, speaker_wav: str, output_path: str, language: str, segment_index: int = None, total_segments: int = None) -> bool:
        """
        Генерирует TTS через venv_tts используя subprocess.
        
        Args:
            text: Текст для генерации
            speaker_wav: Референсное аудио
            output_path: Путь для сохранения
            language: Язык
            segment_index: Индекс сегмента (для логирования)
            total_segments: Всего сегментов (для логирования)
        
        Returns:
            True если успешно, False если ошибка
        """
        if not self.venv_tts_path:
            return False
        
        # Путь к скрипту-воркеру (используем абсолютный путь для надежности)
        worker_script = Path(__file__).parent.absolute() / "tts_worker.py"
        
        if not worker_script.exists():
            self._log(f"❌ Скрипт tts_worker.py не найден: {worker_script}")
            return False
        
        start_time = time.time()
        
        try:
            # Подготавливаем данные для передачи
            input_data = {
                "text": text,
                "speaker_wav": speaker_wav,
                "output_path": output_path,
                "language": language,
                "model_name": self.model_name
            }
            
            # Добавляем информацию о сегменте для логирования в worker
            if segment_index is not None and total_segments is not None:
                input_data["segment_info"] = {
                    "index": segment_index + 1,
                    "total": total_segments
                }
            
            # Запускаем скрипт через venv_tts Python
            result = subprocess.run(
                [str(self.venv_tts_path), str(worker_script)],
                input=json.dumps(input_data),
                capture_output=True,
                text=True,
                timeout=300  # 5 минут максимум на сегмент
            )
            
            # Логируем stderr (там выводятся сообщения из worker)
            if result.stderr:
                for line in result.stderr.strip().split('\n'):
                    if line.strip():
                        self._log(line)
            
            if result.returncode != 0:
                self._log(f"❌ Ошибка subprocess (код {result.returncode}): {result.stderr}")
                return False
            
            # Парсим результат
            output = json.loads(result.stdout)
            
            if not output.get("success"):
                self._log(f"❌ Ошибка генерации TTS: {output.get('error', 'Unknown error')}")
                return False
            
            # Логируем время выполнения
            total_time = time.time() - start_time
            load_time = output.get("load_time", 0)
            gen_time = output.get("gen_time", 0)
            
            if segment_index is not None and total_segments is not None:
                progress = ((segment_index + 1) / total_segments) * 100
                if load_time > 0:
                    self._log(f"   ⏱️ Время: загрузка {load_time:.1f}с + генерация {gen_time:.1f}с = {total_time:.1f}с | Прогресс: {progress:.1f}%")
                else:
                    self._log(f"   ⏱️ Время: генерация {gen_time:.1f}с (модель уже загружена) | Прогресс: {progress:.1f}%")
            
            return True
            
        except subprocess.TimeoutExpired:
            self._log(f"❌ Таймаут генерации TTS (превышено 5 минут)")
            return False
        except json.JSONDecodeError as e:
            self._log(f"❌ Ошибка парсинга результата: {e}")
            return False
        except Exception as e:
            self._log(f"❌ Ошибка subprocess: {e}")
            return False
    
    def extract_speaker_samples(
        self,
        audio_path: str,
        segments: List[Dict]
    ) -> Dict[str, str]:
        """
        Извлекает референсные аудио для каждого уникального спикера.
        
        Ищет сегменты длительностью 3-10 секунд (оптимально для обучения модели спикера).
        Приоритет: чем больше в этом диапазоне - тем лучше.
        Если таких нет, берет самый длинный доступный сегмент.
        
        Args:
            audio_path: Путь к исходному аудио файлу
            segments: Список сегментов с информацией о спикерах и таймингах
            
        Returns:
            Словарь {speaker_id: path_to_sample.wav}
        """
        self._log(f"🎯 Извлечение референсных аудио для спикеров...")
        
        if not segments:
            self._log("⚠️ Нет сегментов для обработки")
            return {}
        
        # Загружаем исходное аудио
        try:
            audio = AudioSegment.from_file(audio_path)
            self._log(f"✅ Исходное аудио загружено: {len(audio) / 1000:.1f} сек")
        except Exception as e:
            self._log(f"❌ Ошибка загрузки аудио: {e}")
            return {}
        
        # Группируем сегменты по спикерам
        speaker_segments = {}
        for seg in segments:
            speaker = seg.get("speaker", "SPEAKER_UNKNOWN")
            if speaker not in speaker_segments:
                speaker_segments[speaker] = []
            speaker_segments[speaker].append(seg)
        
        self._log(f"📊 Найдено спикеров: {len(speaker_segments)}")
        
        speaker_samples = {}
        
        for speaker, segs in speaker_segments.items():
            # Ищем оптимальный сегмент (3-10 секунд, чем больше - тем лучше)
            best_seg = None
            best_duration = 0
            
            for seg in segs:
                start = float(seg.get("start", 0))
                end = float(seg.get("end", 0))
                duration = end - start
                
                # Оптимальный диапазон для обучения модели спикера: 3-10 секунд
                # Приоритет: чем больше в этом диапазоне - тем лучше
                if 3.0 <= duration <= 10.0:
                    # Если это лучший сегмент в диапазоне (длиннее предыдущего)
                    if duration > best_duration:
                        best_seg = seg
                        best_duration = duration
                        # Не останавливаемся, продолжаем искать более длинный в диапазоне
                
                # Если нет сегментов в оптимальном диапазоне, запоминаем самый длинный
                elif best_duration == 0 and duration > best_duration:
                    best_duration = duration
                    best_seg = seg
            
            if best_seg is None:
                self._log(f"⚠️ Не найдено сегментов для {speaker}")
                continue
            
            # Извлекаем аудио сегмент
            start_ms = int(best_seg.get("start", 0) * 1000)
            end_ms = int(best_seg.get("end", 0) * 1000)
            
            try:
                sample_audio = audio[start_ms:end_ms]
                
                # Сохраняем референсный файл
                sample_path = self.voices_dir / f"{speaker}_sample.wav"
                sample_audio.export(str(sample_path), format="wav")
                
                speaker_samples[speaker] = str(sample_path)
                
                self._log(
                    f"✅ Референс для {speaker}: {best_duration:.1f}с "
                    f"({sample_path.name})"
                )
            except Exception as e:
                self._log(f"❌ Ошибка извлечения аудио для {speaker}: {e}")
                continue
        
        self._log(f"🎯 Извлечено референсов: {len(speaker_samples)}/{len(speaker_segments)}")
        return speaker_samples
    
    def generate_dubbing(
        self,
        segments: List[Dict],
        speaker_samples: Dict[str, str],
        target_lang: str = "ru"
    ) -> List[Dict]:
        """
        Генерирует дубляж для всех сегментов с клонированием голоса.
        
        Args:
            segments: Список сегментов с переведенным текстом
            speaker_samples: Словарь {speaker_id: path_to_sample.wav}
            target_lang: Целевой язык для генерации (по умолчанию "ru")
            
        Returns:
            Обновленный список сегментов с добавленным ключом "audio_file"
        """
        if not segments:
            self._log("⚠️ Нет сегментов для генерации дубляжа")
            return segments
        
        # Загружаем модель (ленивая загрузка)
        self._load_model()
        
        if not speaker_samples:
            self._log("⚠️ Нет референсных аудио для спикеров")
            return segments
        
        total_segments = len(segments)
        self._log(f"🎬 Генерация дубляжа для {total_segments} сегментов...")
        
        # Fallback: если для спикера нет референса, используем первый доступный
        fallback_sample = list(speaker_samples.values())[0] if speaker_samples else None
        
        updated_segments = []
        success_count = 0
        error_count = 0
        start_time = time.time()
        
        for i, seg in enumerate(segments):
            # Проверяем флаг остановки в цикле
            if self.should_stop_callback and self.should_stop_callback():
                self._log("⏹️ Генерация дубляжа прервана пользователем")
                raise InterruptedError("Processing stopped by user")
            
            speaker = seg.get("speaker", "SPEAKER_UNKNOWN")
            text = seg.get("text", "").strip()
            
            if not text:
                self._log(f"⚠️ [{i+1}/{total_segments}] Сегмент {i}: пустой текст, пропускаем")
                updated_segments.append(seg)
                continue
            
            # Определяем референсный файл для спикера
            speaker_wav = speaker_samples.get(speaker, fallback_sample)
            
            if not speaker_wav or not os.path.exists(speaker_wav):
                self._log(f"⚠️ [{i+1}/{total_segments}] Сегмент {i}: нет референса для {speaker}, пропускаем")
                updated_segments.append(seg)
                error_count += 1
                continue
            
            # Генерируем аудио
            output_path = self.temp_tts_dir / f"segment_{i:04d}.wav"
            
            try:
                # Вычисляем прогресс
                progress = ((i + 1) / total_segments) * 100
                elapsed = time.time() - start_time
                avg_time_per_segment = elapsed / (i + 1) if i > 0 else 0
                remaining_segments = total_segments - (i + 1)
                estimated_remaining = avg_time_per_segment * remaining_segments
                
                self._log(f"🎤 [{i+1}/{total_segments}] ({progress:.1f}%) {speaker} | {len(text)} символов | ⏱️ ~{estimated_remaining/60:.1f} мин осталось")
                
                # Генерация TTS
                if self.use_venv_tts:
                    # Используем venv_tts через subprocess
                    success = self._generate_tts_via_venv(
                        text=text,
                        speaker_wav=speaker_wav,
                        output_path=str(output_path),
                        language=target_lang,
                        segment_index=i,
                        total_segments=total_segments
                    )
                    if not success:
                        raise Exception("Ошибка генерации через venv_tts")
                else:
                    # Используем прямой вызов TTS API
                    seg_start = time.time()
                    self.model.tts_to_file(
                        text=text,
                        speaker_wav=speaker_wav,
                        language=target_lang,
                        file_path=str(output_path),
                        split_sentences=False  # Важно! Мы сами разбиваем на предложения
                    )
                    seg_time = time.time() - seg_start
                    self._log(f"   ⏱️ Время генерации: {seg_time:.1f}с")
                
                # Обновляем сегмент
                seg_copy = seg.copy()
                seg_copy["audio_file"] = str(output_path)
                updated_segments.append(seg_copy)
                
                success_count += 1
                
            except InterruptedError:
                self._log("⏹️ Генерация дубляжа прервана пользователем")
                raise
            except Exception as e:
                self._log(f"❌ [{i+1}/{total_segments}] Ошибка генерации для сегмента {i} ({speaker}): {e}")
                # Добавляем сегмент без аудио, чтобы не потерять данные
                updated_segments.append(seg)
                error_count += 1
                continue
        
        total_time = time.time() - start_time
        self._log(
            f"✅ Генерация завершена: успешно {success_count}/{total_segments}, "
            f"ошибок {error_count} | Общее время: {total_time/60:.1f} мин ({total_time:.1f}с)"
        )
        
        return updated_segments
    
    def merge_audio_segments(
        self,
        segments: List[Dict],
        output_path: str
    ) -> str:
        """
        Объединяет все аудио сегменты в один финальный файл.
        
        Args:
            segments: Список сегментов с ключом "audio_file"
            output_path: Путь для сохранения финального аудио
            original_audio_path: Опционально - путь к исходному аудио (для синхронизации)
            
        Returns:
            Путь к созданному файлу
        """
        self._log(f"🎬 Объединение {len(segments)} аудио сегментов...")
        
        if not segments:
            raise ValueError("Нет сегментов для объединения")
        
        # Собираем все аудио файлы в правильном порядке
        audio_segments = []
        missing_files = []
        
        for i, seg in enumerate(segments):
            audio_file = seg.get("audio_file")
            if not audio_file or not os.path.exists(audio_file):
                missing_files.append(i)
                # Создаем тишину для пропущенных сегментов
                start = float(seg.get("start", 0))
                end = float(seg.get("end", start + 1.0))
                duration_ms = int((end - start) * 1000)
                silence = AudioSegment.silent(duration=duration_ms)
                audio_segments.append(silence)
                self._log(f"⚠️ Сегмент {i}: файл отсутствует, добавлена тишина ({duration_ms}ms)")
            else:
                try:
                    audio = AudioSegment.from_file(audio_file)
                    audio_segments.append(audio)
                except Exception as e:
                    self._log(f"⚠️ Ошибка загрузки сегмента {i}: {e}")
                    # Добавляем тишину вместо ошибки
                    start = float(seg.get("start", 0))
                    end = float(seg.get("end", start + 1.0))
                    duration_ms = int((end - start) * 1000)
                    silence = AudioSegment.silent(duration=duration_ms)
                    audio_segments.append(silence)
        
        if missing_files:
            self._log(f"⚠️ Пропущено файлов: {len(missing_files)}")
        
        # Объединяем все сегменты
        if not audio_segments:
            raise ValueError("Нет аудио для объединения")
        
        self._log(f"🔗 Склейка {len(audio_segments)} сегментов...")
        final_audio = sum(audio_segments)
        
        # Экспортируем финальный файл
        output_path_obj = Path(output_path)
        output_path_obj.parent.mkdir(parents=True, exist_ok=True)
        
        final_audio.export(str(output_path), format="wav")
        
        duration_sec = len(final_audio) / 1000.0
        self._log(f"✅ Финальное аудио создано: {output_path}")
        self._log(f"   Длительность: {duration_sec:.1f} секунд")
        
        return str(output_path)
    
    def merge_audio_segments(
        self,
        segments: List[Dict],
        output_path: str
    ) -> str:
        """
        Объединяет все аудио сегменты в один финальный файл.
        
        Args:
            segments: Список сегментов с ключом "audio_file"
            output_path: Путь для сохранения финального аудио
            
        Returns:
            Путь к созданному файлу
        """
        self._log(f"🎬 Объединение {len(segments)} аудио сегментов...")
        
        if not segments:
            raise ValueError("Нет сегментов для объединения")
        
        # Собираем все аудио файлы в правильном порядке
        audio_segments = []
        missing_files = []
        
        for i, seg in enumerate(segments):
            audio_file = seg.get("audio_file")
            if not audio_file or not os.path.exists(audio_file):
                missing_files.append(i)
                # Создаем тишину для пропущенных сегментов
                start = float(seg.get("start", 0))
                end = float(seg.get("end", start + 1.0))
                duration_ms = int((end - start) * 1000)
                silence = AudioSegment.silent(duration=duration_ms)
                audio_segments.append(silence)
                self._log(f"⚠️ Сегмент {i}: файл отсутствует, добавлена тишина ({duration_ms}ms)")
            else:
                try:
                    audio = AudioSegment.from_file(audio_file)
                    audio_segments.append(audio)
                except Exception as e:
                    self._log(f"⚠️ Ошибка загрузки сегмента {i}: {e}")
                    # Добавляем тишину вместо ошибки
                    start = float(seg.get("start", 0))
                    end = float(seg.get("end", start + 1.0))
                    duration_ms = int((end - start) * 1000)
                    silence = AudioSegment.silent(duration=duration_ms)
                    audio_segments.append(silence)
        
        if missing_files:
            self._log(f"⚠️ Пропущено файлов: {len(missing_files)}")
        
        # Объединяем все сегменты
        if not audio_segments:
            raise ValueError("Нет аудио для объединения")
        
        self._log(f"🔗 Склейка {len(audio_segments)} сегментов...")
        final_audio = sum(audio_segments)
        
        # Экспортируем финальный файл
        output_path_obj = Path(output_path)
        output_path_obj.parent.mkdir(parents=True, exist_ok=True)
        
        final_audio.export(str(output_path), format="wav")
        
        duration_sec = len(final_audio) / 1000.0
        self._log(f"✅ Финальное аудио создано: {output_path}")
        self._log(f"   Длительность: {duration_sec:.1f} секунд")
        
        return str(output_path)
    
    def cleanup_temp_files(self):
        """Очищает временные файлы TTS"""
        try:
            if self.temp_tts_dir.exists():
                for file in self.temp_tts_dir.glob("*.wav"):
                    file.unlink()
                self._log(f"🧹 Временные файлы TTS очищены")
        except Exception as e:
            self._log(f"⚠️ Ошибка очистки временных файлов: {e}")

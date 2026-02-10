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
from core.config import APP_PATHS

# Перенаправляем кэш Coqui TTS в папку приложения (до импорта TTS!)
_tts_cache_dir = str(APP_PATHS["models"] / "tts")
os.environ.setdefault("COQUI_TTS_CACHE", _tts_cache_dir)

# Пытаемся импортировать TTS (поддерживаем и старый TTS, и новый coqui-tts)
TTS_AVAILABLE = False
TTS = None
TTS_ERROR = None

try:
    # Пробуем импортировать TTS API
    from TTS.api import TTS
    TTS_AVAILABLE = True

    # Патчим директорию кэша моделей на папку приложения
    try:
        import TTS.utils.manage as _tts_manage
        _original_get_user_data_dir = _tts_manage.get_user_data_dir
        def _patched_get_user_data_dir(appname):
            custom = os.environ.get("COQUI_TTS_CACHE")
            if custom and appname == "tts":
                os.makedirs(custom, exist_ok=True)
                return custom
            return _original_get_user_data_dir(appname)
        _tts_manage.get_user_data_dir = _patched_get_user_data_dir
    except Exception:
        pass  # Не критично — модель скачается в стандартную папку

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

# Максимальное количество попыток загрузки модели (при сетевых ошибках)
MAX_MODEL_LOAD_RETRIES = 3


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
        
        # Создаем необходимые директории (используем безопасные пути из config)
        self.voices_dir = APP_PATHS['base'] / "voices"
        try:
            self.voices_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self._log(f"⚠️ Ошибка создания папки voices: {e}")
            
        self.temp_tts_dir = APP_PATHS['temp'] / "tts_parts"
        try:
            self.temp_tts_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self._log(f"⚠️ Ошибка создания папки temp/tts_parts: {e}")
        
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
        """Ленивая загрузка модели XTTS с retry при сетевых ошибках"""
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

        last_error = None
        for attempt in range(1, MAX_MODEL_LOAD_RETRIES + 1):
            try:
                self.model = TTS(model_name=self.model_name, progress_bar=False)
                self.model.to(self.device)
                self._log(f"✅ Модель XTTS загружена на {self.device}")
                return
            except (ConnectionError, OSError, TimeoutError) as e:
                # Сетевые ошибки — retry с backoff
                last_error = e
                if attempt < MAX_MODEL_LOAD_RETRIES:
                    wait = 5 * (2 ** (attempt - 1))  # 5, 10, 20 сек
                    self._log(
                        f"⚠️ Попытка {attempt}/{MAX_MODEL_LOAD_RETRIES} не удалась: {e}\n"
                        f"   Повтор через {wait} сек..."
                    )
                    time.sleep(wait)
                else:
                    self._log(
                        f"❌ Не удалось загрузить модель XTTS после {MAX_MODEL_LOAD_RETRIES} попыток.\n"
                        f"   Ошибка: {e}\n"
                        f"   Проверьте интернет-соединение и попробуйте снова."
                    )
            except Exception as e:
                # urllib3, requests и другие ошибки загрузки
                err_str = str(e).lower()
                is_network = any(kw in err_str for kw in [
                    "download", "connection", "timeout", "urllib3",
                    "requests", "ssl", "socket", "network", "failed to download",
                ])
                if is_network and attempt < MAX_MODEL_LOAD_RETRIES:
                    last_error = e
                    wait = 5 * (2 ** (attempt - 1))
                    self._log(
                        f"⚠️ Попытка {attempt}/{MAX_MODEL_LOAD_RETRIES} не удалась (сеть): {e}\n"
                        f"   Повтор через {wait} сек..."
                    )
                    time.sleep(wait)
                else:
                    self._log(f"❌ Ошибка загрузки модели XTTS: {e}")
                    raise

        raise RuntimeError(
            f"Не удалось загрузить модель XTTS после {MAX_MODEL_LOAD_RETRIES} попыток: {last_error}"
        )
    
    def _generate_tts_via_venv(self, text: str, speaker_wav: str, output_path: str, language: str, segment_index: int = None, total_segments: int = None, tts_speed: float = 1.0) -> bool:
        """
        Генерирует TTS через venv_tts используя subprocess.

        Args:
            text: Текст для генерации
            speaker_wav: Референсное аудио
            output_path: Путь для сохранения
            language: Язык
            segment_index: Индекс сегмента (для логирования)
            total_segments: Всего сегментов (для логирования)
            tts_speed: Скорость речи (1.0 = нормально, до 1.15 для ускорения)

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
                "model_name": self.model_name,
                "tts_speed": tts_speed
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

        XTTS v2 работает лучше с референсами 6-12 секунд чистой речи.
        Стратегия: объединяем несколько коротких сегментов одного спикера,
        чтобы получить более длинный и качественный референс.

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

        # Оптимальные параметры для XTTS v2
        MIN_REFERENCE_DURATION = 6.0    # Минимум 6 секунд для хорошего клонирования
        MAX_REFERENCE_DURATION = 15.0   # Максимум 15 секунд (больше не нужно)
        IDEAL_SEGMENT_DURATION = (4.0, 12.0)  # Идеальный диапазон одного сегмента

        for speaker, segs in speaker_segments.items():
            # Сортируем сегменты по длительности (длинные первые)
            sorted_segs = sorted(
                segs,
                key=lambda s: float(s.get("end", 0)) - float(s.get("start", 0)),
                reverse=True
            )

            # Стратегия 1: ищем один идеальный сегмент (4-12 секунд)
            best_single_seg = None
            for seg in sorted_segs:
                duration = float(seg.get("end", 0)) - float(seg.get("start", 0))
                if IDEAL_SEGMENT_DURATION[0] <= duration <= IDEAL_SEGMENT_DURATION[1]:
                    best_single_seg = seg
                    break

            if best_single_seg:
                # Нашли идеальный одиночный сегмент
                start_ms = int(best_single_seg.get("start", 0) * 1000)
                end_ms = int(best_single_seg.get("end", 0) * 1000)
                duration = (end_ms - start_ms) / 1000.0

                try:
                    sample_audio = audio[start_ms:end_ms]
                    sample_audio = self._enhance_reference_audio(sample_audio)

                    sample_path = self.voices_dir / f"{speaker}_sample.wav"
                    sample_audio.export(str(sample_path), format="wav", parameters=["-ar", "22050", "-ac", "1"])

                    speaker_samples[speaker] = str(sample_path)
                    self._log(f"✅ Референс для {speaker}: {duration:.1f}с (идеальный сегмент)")
                    continue
                except Exception as e:
                    self._log(f"⚠️ Ошибка извлечения сегмента для {speaker}: {e}")

            # Стратегия 2: объединяем несколько сегментов до нужной длины
            combined_audio = AudioSegment.empty()
            combined_duration = 0.0
            segments_used = 0

            for seg in sorted_segs:
                if combined_duration >= MAX_REFERENCE_DURATION:
                    break

                start_ms = int(seg.get("start", 0) * 1000)
                end_ms = int(seg.get("end", 0) * 1000)
                seg_duration = (end_ms - start_ms) / 1000.0

                # Пропускаем очень короткие сегменты (< 1 сек) - много шума
                if seg_duration < 1.0:
                    continue

                try:
                    seg_audio = audio[start_ms:end_ms]

                    # Добавляем короткую паузу между сегментами
                    if len(combined_audio) > 0:
                        combined_audio += AudioSegment.silent(duration=200)  # 200ms пауза

                    combined_audio += seg_audio
                    combined_duration += seg_duration
                    segments_used += 1
                except Exception:
                    continue

            if combined_duration < 2.0:
                self._log(f"⚠️ Недостаточно аудио для {speaker} (только {combined_duration:.1f}с)")
                continue

            # Обрезаем если слишком длинный
            if combined_duration > MAX_REFERENCE_DURATION:
                combined_audio = combined_audio[:int(MAX_REFERENCE_DURATION * 1000)]
                combined_duration = MAX_REFERENCE_DURATION

            try:
                # Улучшаем качество референса
                combined_audio = self._enhance_reference_audio(combined_audio)

                sample_path = self.voices_dir / f"{speaker}_sample.wav"
                # Экспортируем в формате оптимальном для XTTS: 22050 Hz, mono
                combined_audio.export(
                    str(sample_path),
                    format="wav",
                    parameters=["-ar", "22050", "-ac", "1"]
                )

                speaker_samples[speaker] = str(sample_path)
                self._log(
                    f"✅ Референс для {speaker}: {combined_duration:.1f}с "
                    f"(объединено {segments_used} сегментов)"
                )
            except Exception as e:
                self._log(f"❌ Ошибка сохранения референса для {speaker}: {e}")
                continue

        self._log(f"🎯 Извлечено референсов: {len(speaker_samples)}/{len(speaker_segments)}")
        return speaker_samples

    def _enhance_reference_audio(self, audio: AudioSegment) -> AudioSegment:
        """
        Улучшает качество референсного аудио для лучшего клонирования.

        - Конвертация в моно
        - Нормализация громкости
        - Удаление тишины в начале/конце
        - Компрессия динамического диапазона (опционально)
        """
        try:
            from pydub.effects import normalize, strip_silence, compress_dynamic_range

            # Конвертируем в моно если стерео
            if audio.channels > 1:
                audio = audio.set_channels(1)

            # Нормализуем громкость
            audio = normalize(audio)

            # Удаляем тишину в начале и конце
            # silence_thresh=-40 dB, оставляем минимум 50ms тишины
            audio = strip_silence(audio, silence_len=50, silence_thresh=-40, padding=50)

            # Лёгкая компрессия для выравнивания громкости речи
            # (помогает когда спикер говорит то тихо, то громко)
            try:
                audio = compress_dynamic_range(audio, threshold=-20.0, ratio=2.0, attack=5.0, release=50.0)
            except Exception:
                pass  # Некоторые версии pydub не поддерживают

            return audio
        except ImportError:
            # pydub.effects может быть недоступен
            return audio
        except Exception as e:
            self._log(f"⚠️ Не удалось улучшить референс: {e}")
            return audio
    
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
        
        # Нормализуем язык: преобразуем RUSSIAN -> ru и т.д.
        lang_map = {
            'RUSSIAN': 'ru',
            'ENGLISH': 'en',
            'SPANISH': 'es',
            'FRENCH': 'fr',
            'GERMAN': 'de',
            'ITALIAN': 'it',
            'PORTUGUESE': 'pt',
            'POLISH': 'pl',
            'TURKISH': 'tr',
            'DUTCH': 'nl',
            'CZECH': 'cs',
            'ARABIC': 'ar',
            'CHINESE': 'zh-cn',
            'HUNGARIAN': 'hu',
            'KOREAN': 'ko',
            'JAPANESE': 'ja',
            'HINDI': 'hi'
        }
        target_lang = lang_map.get(target_lang.upper(), target_lang.lower())
        
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
            tts_speed = float(seg.get("tts_speed", 1.0))  # Скорость из умного перевода
            
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
                        total_segments=total_segments,
                        tts_speed=tts_speed
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
    
    def cleanup_temp_files(self):
        """Очищает временные файлы TTS"""
        try:
            if self.temp_tts_dir.exists():
                for file in self.temp_tts_dir.glob("*.wav"):
                    file.unlink()
                self._log(f"🧹 Временные файлы TTS очищены")
        except Exception as e:
            self._log(f"⚠️ Ошибка очистки временных файлов: {e}")

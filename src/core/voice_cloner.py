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
import struct
import numpy as np
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
MAX_MODEL_LOAD_RETRIES = 5


def _cleanup_tts_partial_download():
    """Удаляет частично скачанные файлы модели XTTS перед повторной попыткой."""
    import shutil
    cache_dir = os.environ.get("COQUI_TTS_CACHE", "")
    if not cache_dir or not os.path.isdir(cache_dir):
        return
    model_dir = os.path.join(cache_dir, "tts_models--multilingual--multi-dataset--xtts_v2")
    if os.path.exists(model_dir):
        has_config = os.path.exists(os.path.join(model_dir, "config.json"))
        has_model = any(f.endswith(".pth") for f in os.listdir(model_dir)) if os.path.isdir(model_dir) else False
        if not (has_config and has_model):
            try:
                shutil.rmtree(model_dir)
            except Exception:
                pass
    # Чистим .zip промежуточные файлы
    for f in os.listdir(cache_dir):
        if f.endswith(".zip") and "xtts" in f.lower():
            try:
                os.unlink(os.path.join(cache_dir, f))
            except Exception:
                pass


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
            gpu_name = torch.cuda.get_device_name(0)
            self._log(f"🟢 NVIDIA GPU: {gpu_name} (CUDA {torch.version.cuda})")
            return "cuda"

        # Диагностика: почему CUDA не доступна
        cuda_built = getattr(torch.version, 'cuda', None)
        if cuda_built:
            self._log(f"⚠️ PyTorch собран с CUDA {cuda_built}, но GPU не обнаружен. Проверьте драйверы NVIDIA.")
        elif sys.platform == 'win32':
            self._log("⚠️ PyTorch установлен без CUDA. TTS будет работать на CPU (медленнее).")

        # Для Mac проверяем MPS, но по умолчанию используем CPU для стабильности
        if hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
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
                if attempt > 1:
                    _cleanup_tts_partial_download()
                self.model = TTS(model_name=self.model_name, progress_bar=False)
                self.model.to(self.device)
                self._log(f"✅ Модель XTTS загружена на {self.device}")
                return
            except Exception as e:
                err_str = str(e).lower()
                is_network = (
                    isinstance(e, (ConnectionError, OSError, TimeoutError))
                    or any(kw in err_str for kw in [
                        "download", "connection", "timeout", "urllib3",
                        "requests", "ssl", "socket", "network", "failed to download",
                    ])
                )
                last_error = e
                if is_network and attempt < MAX_MODEL_LOAD_RETRIES:
                    wait = 10 * (2 ** (attempt - 1))  # 10, 20, 40, 80 сек
                    self._log(
                        f"⚠️ Попытка {attempt}/{MAX_MODEL_LOAD_RETRIES} не удалась (сеть): {e}\n"
                        f"   Повтор через {wait} сек..."
                    )
                    time.sleep(wait)
                else:
                    if is_network:
                        self._log(
                            f"❌ Не удалось скачать модель XTTS после {MAX_MODEL_LOAD_RETRIES} попыток.\n"
                            f"   Ошибка: {e}\n"
                            f"   Проверьте интернет-соединение и попробуйте снова."
                        )
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
    
    def extract_speaker_samples_for_gender(
        self,
        audio_path: str,
        segments: List[Dict]
    ) -> Dict[str, str]:
        """
        Быстрая извлечка минимальных аудио-сэмплов только для определения пола.
        Без тяжёлой обработки (enhance, компрессия) — нужна только pitch-детекция.
        """
        self._log("🔍 Быстрая извлечка аудио для определения пола спикеров...")

        if not segments:
            return {}

        try:
            audio = AudioSegment.from_file(audio_path)
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

        speaker_samples = {}

        for speaker, segs in speaker_segments.items():
            # Берём самый длинный сегмент (минимум 2 сек для pitch-анализа)
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
                    # Минимальная обработка: только моно + ресэмпл
                    sample_audio = sample_audio.set_channels(1)

                    sample_path = self.voices_dir / f"{speaker}_gender_sample.wav"
                    sample_audio.export(str(sample_path), format="wav", parameters=["-ar", "16000", "-ac", "1"])
                    speaker_samples[speaker] = str(sample_path)
                    self._log(f"   {speaker}: {duration:.1f}с сэмпл для анализа")
                    break
                except Exception:
                    continue

            if speaker not in speaker_samples:
                self._log(f"⚠️ {speaker}: недостаточно аудио для определения пола")

        return speaker_samples

    # ── Preset Voices (готовые голоса) ──────────────────────────────────────

    # Текст для генерации референсных сэмплов через edge-tts (≈10 секунд речи)
    _PRESET_TEXT_MALE = (
        "Добрый день, уважаемые зрители. Сегодня мы рассмотрим очень интересную тему, "
        "которая касается каждого из нас. Давайте разберёмся в деталях и постараемся "
        "понять основные принципы."
    )
    _PRESET_TEXT_FEMALE = (
        "Здравствуйте, дорогие друзья. Я рада приветствовать вас на нашем канале. "
        "Сегодня у нас важная и увлекательная тема. Надеюсь, вам будет интересно "
        "и полезно узнать обо всём подробнее."
    )

    def _get_presets_dir(self) -> Path:
        """Возвращает директорию для хранения пресетных голосов."""
        presets_dir = self.voices_dir / "presets"
        presets_dir.mkdir(parents=True, exist_ok=True)
        return presets_dir

    def _ensure_preset_voices(self) -> Dict[str, str]:
        """
        Проверяет наличие пресетных голосов и генерирует их через edge-tts если нет.

        Returns:
            Словарь {"male": path, "female": path}
        """
        presets_dir = self._get_presets_dir()
        male_path = presets_dir / "male_ru.wav"
        female_path = presets_dir / "female_ru.wav"

        # Если оба файла уже есть — возвращаем
        if male_path.exists() and female_path.exists():
            self._log("✅ Пресетные голоса найдены в кэше")
            return {"male": str(male_path), "female": str(female_path)}

        self._log("📥 Генерация пресетных голосов через Microsoft Edge TTS...")

        try:
            import edge_tts
        except ImportError:
            self._log("📦 Установка edge-tts...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", "edge-tts"])
            import edge_tts

        import asyncio

        async def _generate_preset(voice: str, text: str, output_path: Path):
            """Генерация одного пресетного голоса."""
            mp3_path = output_path.with_suffix(".mp3")
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(str(mp3_path))
            # Конвертируем MP3 → WAV (22050 Hz, mono) для XTTS
            audio = AudioSegment.from_mp3(str(mp3_path))
            audio = audio.set_channels(1).set_frame_rate(22050)
            audio.export(str(output_path), format="wav")
            mp3_path.unlink(missing_ok=True)

        async def _generate_all():
            if not male_path.exists():
                self._log("🎤 Генерация мужского голоса (ru-RU-DmitryNeural)...")
                await _generate_preset("ru-RU-DmitryNeural", self._PRESET_TEXT_MALE, male_path)
                self._log(f"✅ Мужской голос сохранён: {male_path}")

            if not female_path.exists():
                self._log("🎤 Генерация женского голоса (ru-RU-SvetlanaNeural)...")
                await _generate_preset("ru-RU-SvetlanaNeural", self._PRESET_TEXT_FEMALE, female_path)
                self._log(f"✅ Женский голос сохранён: {female_path}")

        # Запускаем async генерацию
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
        Определяет пол спикера по высоте голоса (fundamental frequency).
        Использует автокорреляцию с коррекцией октавных ошибок.

        Мужской голос: F0 ≈ 85-155 Гц
        Женский голос: F0 ≈ 165-255 Гц
        Порог: 160 Гц

        Returns:
            "male" или "female"
        """
        try:
            audio = AudioSegment.from_file(audio_path)
            audio = audio.set_channels(1).set_frame_rate(16000).set_sample_width(2)

            # Извлекаем PCM данные как numpy массив
            samples = np.array(struct.unpack(
                f"<{len(audio.raw_data) // 2}h", audio.raw_data
            ), dtype=np.float64)

            if len(samples) < 1600:
                return "male"

            # Нормализуем
            samples = samples / (np.max(np.abs(samples)) + 1e-10)

            sample_rate = 16000
            # Диапазон: 60-300 Гц (покрывает и мужские и женские голоса)
            min_lag = sample_rate // 300  # 53 сэмплов (300 Гц)
            max_lag = sample_rate // 60   # 266 сэмплов (60 Гц)

            # Фреймы по 50мс (800 сэмплов) — длиннее для надёжности на низких частотах
            frame_size = int(sample_rate * 0.05)
            hop_size = int(sample_rate * 0.02)

            f0_values = []
            for start in range(0, len(samples) - frame_size, hop_size):
                frame = samples[start:start + frame_size]

                # Проверяем что фрейм содержит речь (не тишина)
                energy = np.mean(frame ** 2)
                if energy < 0.001:
                    continue

                # Автокорреляция
                corr = np.correlate(frame, frame, mode='full')
                corr = corr[len(corr) // 2:]  # Правая половина

                if max_lag >= len(corr):
                    continue

                # Нормализуем автокорреляцию относительно лага 0
                if corr[0] <= 0:
                    continue
                corr_norm = corr / corr[0]

                # Ищем пик в нужном диапазоне лагов
                search_region = corr_norm[min_lag:max_lag + 1]
                if len(search_region) == 0:
                    continue

                peak_idx = np.argmax(search_region) + min_lag
                peak_val = corr_norm[peak_idx]

                # Порог значимости пика
                if peak_val < 0.25:
                    continue

                # ── Коррекция октавных ошибок ──
                # Если нашли пик при лаге L (частота F), проверяем лаг 2*L (частота F/2).
                # Если при 2*L тоже есть значимый пик — истинный F0 вдвое ниже.
                # Это главная причина ложного определения мужского голоса как женского.
                double_lag = peak_idx * 2
                if double_lag < len(corr_norm):
                    # Ищем пик в окрестности ±3 сэмпла от 2*lag
                    search_lo = max(min_lag, double_lag - 3)
                    search_hi = min(max_lag, double_lag + 3)
                    if search_lo < search_hi and search_hi < len(corr_norm):
                        sub_region = corr_norm[search_lo:search_hi + 1]
                        sub_peak_val = np.max(sub_region)
                        # Если пик на двойном лаге достаточно сильный (>70% от найденного)
                        # → это настоящий F0, а первый пик — гармоника
                        if sub_peak_val > peak_val * 0.7:
                            sub_peak_idx = np.argmax(sub_region) + search_lo
                            peak_idx = sub_peak_idx

                f0 = sample_rate / peak_idx
                if 60 <= f0 <= 300:
                    f0_values.append(f0)

            if not f0_values:
                self._log("⚠️ Не удалось определить F0, используем мужской голос по умолчанию")
                return "male"

            # Медианная F0 (устойчива к выбросам)
            median_f0 = float(np.median(f0_values))
            gender = "female" if median_f0 > 160 else "male"
            self._log(f"   🔊 F0 = {median_f0:.0f} Гц → {'женский' if gender == 'female' else 'мужской'} голос")
            return gender

        except Exception as e:
            self._log(f"⚠️ Ошибка определения пола: {e}, используем мужской по умолчанию")
            return "male"

    def _create_pitch_variant(self, wav_path: str, semitones: float) -> str:
        """
        Создаёт вариант голоса со сдвигом высоты тона.
        Аккуратный pitch shift через изменение sample rate + resample.

        Args:
            wav_path: Путь к исходному WAV
            semitones: Сдвиг в полутонах (например, +1.5 или -1.5)

        Returns:
            Путь к модифицированному WAV файлу
        """
        try:
            audio = AudioSegment.from_file(wav_path)

            # Pitch shift через изменение частоты дискретизации
            # Повышение тона: увеличиваем sample rate, потом ресэмплируем обратно
            # Формула: new_rate = original_rate * 2^(semitones/12)
            original_rate = audio.frame_rate
            shift_factor = 2 ** (semitones / 12.0)
            new_rate = int(original_rate * shift_factor)

            # Меняем frame rate (это сдвигает pitch + меняет скорость)
            shifted = audio._spawn(audio.raw_data, overrides={
                "frame_rate": new_rate
            })

            # Возвращаем к исходной частоте (это нормализует скорость, сохраняя pitch)
            shifted = shifted.set_frame_rate(original_rate)

            # Сохраняем вариант
            variant_name = Path(wav_path).stem + f"_variant_{semitones:+.1f}st.wav"
            variant_path = self._get_presets_dir() / variant_name
            shifted.export(str(variant_path), format="wav", parameters=["-ar", "22050", "-ac", "1"])

            return str(variant_path)

        except Exception as e:
            self._log(f"⚠️ Ошибка создания варианта голоса: {e}")
            return wav_path  # Возвращаем оригинал если не удалось

    def _build_preset_speaker_map(
        self,
        speaker_samples: Dict[str, str],
        preset_voices: Dict[str, str]
    ) -> Dict[str, str]:
        """
        Строит маппинг спикеров на пресетные голоса с учётом пола.
        Если несколько спикеров одного пола — создаёт pitch-варианты.

        Args:
            speaker_samples: Оригинальные референсы {speaker_id: wav_path}
            preset_voices: Пресеты {"male": path, "female": path}

        Returns:
            Новый маппинг {speaker_id: preset_wav_path}
        """
        self._log("🔍 Определение пола спикеров...")

        # Определяем пол каждого спикера
        speaker_genders = {}
        for speaker, sample_path in speaker_samples.items():
            gender = self._detect_gender(sample_path)
            speaker_genders[speaker] = gender
            self._log(f"   {speaker}: {'👨 мужской' if gender == 'male' else '👩 женский'}")

        # Группируем по полу
        male_speakers = [s for s, g in speaker_genders.items() if g == "male"]
        female_speakers = [s for s, g in speaker_genders.items() if g == "female"]

        preset_map = {}

        # Варианты pitch shift для спикеров одного пола (аккуратные сдвиги)
        # Максимум ±2 полутона, чтобы звучало естественно
        pitch_variants = [0, +1.5, -1.5, +2.0, -2.0]

        # Назначаем мужские голоса
        for i, speaker in enumerate(male_speakers):
            if i == 0:
                preset_map[speaker] = preset_voices["male"]
            else:
                semitones = pitch_variants[min(i, len(pitch_variants) - 1)]
                variant = self._create_pitch_variant(preset_voices["male"], semitones)
                preset_map[speaker] = variant
                self._log(f"   {speaker}: мужской вариант ({semitones:+.1f} полутонов)")

        # Назначаем женские голоса
        for i, speaker in enumerate(female_speakers):
            if i == 0:
                preset_map[speaker] = preset_voices["female"]
            else:
                semitones = pitch_variants[min(i, len(pitch_variants) - 1)]
                variant = self._create_pitch_variant(preset_voices["female"], semitones)
                preset_map[speaker] = variant
                self._log(f"   {speaker}: женский вариант ({semitones:+.1f} полутонов)")

        # Если есть спикеры без сэмплов — назначаем мужской по умолчанию
        if not preset_map:
            self._log("⚠️ Не удалось определить пол спикеров, используем мужской голос")
            preset_map["SPEAKER_UNKNOWN"] = preset_voices["male"]

        self._log(f"✅ Готовые голоса назначены: {len(male_speakers)} муж., {len(female_speakers)} жен.")
        return preset_map

    def generate_dubbing(
        self,
        segments: List[Dict],
        speaker_samples: Dict[str, str],
        target_lang: str = "ru",
        use_preset_voices: bool = False
    ) -> List[Dict]:
        """
        Генерирует дубляж для всех сегментов с клонированием голоса.

        Args:
            segments: Список сегментов с переведенным текстом
            speaker_samples: Словарь {speaker_id: path_to_sample.wav}
            target_lang: Целевой язык для генерации (по умолчанию "ru")
            use_preset_voices: Использовать готовые профессиональные голоса вместо клонирования

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
        
        # Если включены готовые голоса — подменяем speaker_samples на пресетные
        if use_preset_voices:
            self._log("🎭 Режим готовых голосов: используем профессиональные пресеты")
            try:
                preset_voices = self._ensure_preset_voices()
                speaker_samples = self._build_preset_speaker_map(speaker_samples, preset_voices)
            except Exception as e:
                self._log(f"❌ Ошибка подготовки пресетных голосов: {e}")
                self._log("⚠️ Переключаемся на клонирование оригинальных голосов")

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
        
        # Объединяем все сегменты с плавными переходами (crossfade)
        if not audio_segments:
            raise ValueError("Нет аудио для объединения")

        CROSSFADE_MS = 30  # Мягкий переход между сегментами

        self._log(f"🔗 Склейка {len(audio_segments)} сегментов с crossfade {CROSSFADE_MS}ms...")

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

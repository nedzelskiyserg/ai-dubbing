# -*- coding: utf-8 -*-
import os
import sys
import gc
import re
import shutil
import platform
import warnings
import traceback
import threading
import time
from typing import Optional, Callable, List, Dict
import torch

from core.config import resolve_path_for_win, APP_PATHS

# Подавляем лишние предупреждения
warnings.filterwarnings('ignore')

# --- ПАТЧ ДЛЯ Windows: symlink → copy fallback ---
# huggingface_hub при скачивании моделей создаёт symlinks,
# но на Windows без Developer Mode / прав администратора это падает с WinError 1314.
# Патчим os.symlink чтобы при ошибке использовать копирование файлов.
if platform.system() == 'Windows':
    _original_symlink = os.symlink

    def _safe_symlink(src, dst, *args, **kwargs):
        try:
            _original_symlink(src, dst, *args, **kwargs)
        except OSError:
            abs_src = os.path.join(os.path.dirname(dst), src) if not os.path.isabs(src) else src
            if os.path.isdir(abs_src):
                shutil.copytree(abs_src, dst)
            else:
                shutil.copy2(abs_src, dst)

    os.symlink = _safe_symlink

# --- ПАТЧ ДЛЯ PyTorch 2.6+ (CRITICAL) ---
# WhisperX и pyannote используют старый способ загрузки весов через pickle.
# В PyTorch 2.6+ дефолт weights_only=True — без патча модели грузятся некорректно,
# что приводит к segfault (0xC0000005) при VAD inference на Windows.
# ВАЖНО: всегда форсируем weights_only=False, даже если аргумент не передан явно.
try:
    import functools
    original_load = torch.load

    @functools.wraps(original_load)
    def patched_load(*args, **kwargs):
        kwargs['weights_only'] = False
        return original_load(*args, **kwargs)

    torch.load = patched_load
except ImportError:
    pass
# -----------------------------------------

class Transcriber:
    """
    Профессиональный транскрибер на базе WhisperX.
    
    Этапы:
    1. Transcribe (Faster-Whisper) - распознавание текста.
    2. Alignment (Wav2Vec2) - посимвольное выравнивание таймингов.
    3. Diarization (PyAnnote) - разделение спикеров (строго через Pyannote).
    4. Smart Split - нарезка на предложения для дубляжа.
    
    Особенности:
    - Полная поддержка Apple Silicon (M1/M2/M3) без крашей.
    - Оптимизация памяти (выгрузка моделей между шагами).
    """
    
    def __init__(
        self,
        model_size: str = "large-v3",
        hf_token: Optional[str] = None,
        progress_callback: Optional[Callable[[str], None]] = None,
        should_stop_callback: Optional[Callable[[], bool]] = None
    ):
        self.model_size = model_size
        self.hf_token = hf_token or os.getenv("HF_TOKEN")
        self.progress_callback = progress_callback
        self.should_stop_callback = should_stop_callback
        
        # Автоопределение устройства (Mac vs Windows)
        self.device, self.compute_type = self._detect_environment()
        
    def _log(self, msg: str):
        """Логирование в UI и консоль"""
        print(msg)  # В консоль
        if self.progress_callback:
            self.progress_callback(msg) # В UI

    def _is_model_cached(self, download_root: str) -> bool:
        """
        Проверяет, скачана ли модель Whisper в локальный кэш.
        Модель считается скачанной если есть snapshot с config.json и model.bin.
        """
        if not download_root:
            return False
        model_dir = os.path.join(
            download_root,
            f"models--Systran--faster-whisper-{self.model_size}",
            "snapshots"
        )
        if not os.path.exists(model_dir):
            return False
        try:
            for snapshot in os.listdir(model_dir):
                snapshot_path = os.path.join(model_dir, snapshot)
                if not os.path.isdir(snapshot_path):
                    continue
                config = os.path.join(snapshot_path, "config.json")
                model_bin = os.path.join(snapshot_path, "model.bin")
                if os.path.exists(config) and os.path.exists(model_bin):
                    return True
        except OSError:
            pass
        return False

    def _download_model_with_progress(self, download_root: str):
        """
        Скачивает модель Whisper с HuggingFace с отображением прогресса.
        Прогресс отслеживается по размеру скачанных blob-файлов.
        """
        from huggingface_hub import snapshot_download

        repo_id = f"Systran/faster-whisper-{self.model_size}"
        blobs_dir = os.path.join(
            download_root,
            f"models--Systran--faster-whisper-{self.model_size}",
            "blobs"
        )

        # Примерные размеры моделей (в байтах)
        model_sizes = {
            "tiny": 150_000_000,
            "base": 290_000_000,
            "small": 950_000_000,
            "medium": 3_000_000_000,
            "large-v2": 6_200_000_000,
            "large-v3": 6_200_000_000,
        }
        expected_size = model_sizes.get(self.model_size, 6_200_000_000)

        # Фоновый поток для отслеживания прогресса
        stop_monitor = threading.Event()
        last_pct = [-1]  # mutable для замыкания

        def _monitor():
            while not stop_monitor.is_set():
                try:
                    total = 0
                    if os.path.exists(blobs_dir):
                        for f in os.listdir(blobs_dir):
                            fp = os.path.join(blobs_dir, f)
                            if os.path.isfile(fp):
                                total += os.path.getsize(fp)
                    pct = int(min(total / expected_size * 100, 99))
                    if pct != last_pct[0] and pct % 5 == 0:
                        last_pct[0] = pct
                        gb_done = total / 1e9
                        gb_total = expected_size / 1e9
                        self._log(f"📥 Скачивание: {pct}% ({gb_done:.1f} / {gb_total:.1f} ГБ)")
                except Exception:
                    pass
                stop_monitor.wait(3)

        monitor = threading.Thread(target=_monitor, daemon=True)
        monitor.start()

        try:
            snapshot_download(
                repo_id,
                cache_dir=download_root,
                local_files_only=False,
            )
            self._log("✅ Модель успешно скачана и сохранена!")
        finally:
            stop_monitor.set()
            monitor.join(timeout=3)

    def _detect_environment(self):
        """
        Определяет железо.
        На Mac (Darwin) используем float32 для максимальной точности alignment.
        float16 крашится на Mac CPU, но float32 работает и намного точнее int8.
        """
        system = platform.system()
        
        if system == "Darwin":
            self._log("🍏 Обнаружена macOS (Apple Silicon).")
            self._log("⚙️ Режим: CPU / float32 (Высокая точность для Alignment)")
            # float32 работает на Mac и дает намного более точные тайминги чем int8
            # Это критично для правильной работы alignment модели
            return "cpu", "float32"
        
        if torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(0)
            self._log(f"🟢 Обнаружена NVIDIA GPU: {gpu_name} (CUDA {torch.version.cuda})")
            return "cuda", "float16"

        # Диагностика: почему CUDA не доступна
        cuda_built = getattr(torch.version, 'cuda', None)
        if cuda_built:
            self._log(f"⚠️ PyTorch собран с CUDA {cuda_built}, но GPU не обнаружен. Проверьте драйверы NVIDIA.")
        else:
            self._log("⚠️ PyTorch установлен без поддержки CUDA (CPU-only). GPU не будет использоваться.")
            self._log("   Для ускорения переустановите приложение — GPU определится автоматически.")
        return "cpu", "float32"

    def _cleanup_memory(self):
        """Очистка памяти от загруженных нейросетей"""
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        if hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
            torch.mps.empty_cache()
    
    def _normalize_language_code(self, lang_code: str) -> str:
        """
        Нормализует код языка для WhisperX alignment.
        Whisper может вернуть 'ru', но alignment может требовать точного кода.
        """
        # Маппинг языков для alignment моделей
        lang_map = {
            'ru': 'ru',  # Русский
            'en': 'en',  # Английский
            'es': 'es',  # Испанский
            'fr': 'fr',  # Французский
            'de': 'de',  # Немецкий
            'it': 'it',  # Итальянский
            'pt': 'pt',  # Португальский
            'pl': 'pl',  # Польский
            'tr': 'tr',  # Турецкий
            'nl': 'nl',  # Голландский
            'cs': 'cs',  # Чешский
            'ar': 'ar',  # Арабский
            'zh': 'zh',  # Китайский
            'ja': 'ja',  # Японский
            'ko': 'ko',  # Корейский
        }
        
        # Если язык есть в маппинге, возвращаем его
        if lang_code in lang_map:
            return lang_map[lang_code]
        
        # Если язык не найден, пробуем первые 2 символа (для случаев типа 'ru-RU')
        lang_base = lang_code.split('-')[0].split('_')[0].lower()
        if lang_base in lang_map:
            return lang_map[lang_base]
        
        # Fallback: возвращаем как есть, но логируем предупреждение
        self._log(f"⚠️ Неизвестный код языка для alignment: {lang_code}, используем как есть")
        return lang_code

    def _load_and_transcribe(self, whisperx, audio_path, language, download_root, batch_size):
        """
        Загружает модель и выполняет транскрипцию.

        Стратегия:
        1. Subprocess-режим (основной): транскрипция в отдельном процессе.
           Если подпроцесс упадёт с segfault (PyAnnote VAD в Parallels/VM),
           API-сервер выживет и повторит с другим VAD (silero).
        2. Direct-режим (fallback): если subprocess недоступен (frozen exe),
           запускаем в текущем процессе с try/except.
        """
        import subprocess as sp
        import json as json_mod

        devices_to_try = [(self.device, self.compute_type)]
        if self.device == "cuda":
            devices_to_try.append(("cpu", "float32"))

        # VAD-методы: pyannote (точнее) → silero (легче, совместимее с VM)
        vad_methods = ["pyannote", "silero"]

        script_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "transcribe_subprocess.py"
        )
        can_subprocess = os.path.exists(script_path) and not getattr(sys, 'frozen', False)

        # ---- SUBPROCESS MODE ----
        if can_subprocess:
            for device, compute_type in devices_to_try:
                for vad_method in vad_methods:
                    self._log(f"⚙️ Транскрипция: device={device}, vad={vad_method}")

                    config = {
                        "model_size": self.model_size,
                        "device": device,
                        "compute_type": compute_type,
                        "language": language,
                        "download_root": download_root,
                        "audio_path": audio_path,
                        "batch_size": batch_size,
                        "vad_method": vad_method,
                        "models_dir": str(APP_PATHS.get("models", "")),
                    }

                    import tempfile
                    pid = os.getpid()
                    tmp = tempfile.gettempdir()
                    config_path = os.path.join(tmp, f"wsx_cfg_{pid}.json")
                    result_path = os.path.join(tmp, f"wsx_res_{pid}.json")

                    with open(config_path, 'w', encoding='utf-8') as f:
                        json_mod.dump(config, f)

                    try:
                        proc = sp.Popen(
                            [sys.executable, script_path, config_path, result_path],
                            stdout=sp.PIPE, stderr=sp.PIPE, text=True,
                        )

                        # Ждём завершения с проверкой stop-callback
                        while proc.poll() is None:
                            if self.should_stop_callback and self.should_stop_callback():
                                proc.terminate()
                                proc.wait(timeout=5)
                                self._cleanup_temp(config_path, result_path)
                                raise InterruptedError("Processing stopped by user")
                            # Читаем stdout подпроцесса (логи)
                            line = proc.stdout.readline()
                            if line:
                                self._log(line.rstrip())
                            time.sleep(0.1)

                        # Дочитываем оставшийся вывод
                        remaining, stderr = proc.communicate(timeout=5)
                        if remaining:
                            for line in remaining.strip().split('\n'):
                                if line:
                                    self._log(line)

                    except InterruptedError:
                        raise
                    except sp.TimeoutExpired:
                        self._log("⚠️ Таймаут транскрипции")
                        proc.kill()
                        self._cleanup_temp(config_path, result_path)
                        continue
                    except Exception as e:
                        self._log(f"⚠️ Ошибка запуска подпроцесса: {e}")
                        self._cleanup_temp(config_path, result_path)
                        continue

                    self._cleanup_temp(config_path)

                    if proc.returncode == 0 and os.path.exists(result_path):
                        try:
                            with open(result_path, 'r', encoding='utf-8') as f:
                                data = json_mod.load(f)
                            self._cleanup_temp(result_path)

                            if data.get("status") == "ok":
                                self.device = device
                                self.compute_type = compute_type
                                self._log(f"✅ Транскрипция завершена ({device}, {vad_method})")
                                return None, {
                                    "segments": data["segments"],
                                    "language": data["language"],
                                }
                            else:
                                self._log(f"⚠️ Ошибка в подпроцессе: {data.get('error', '?')}")
                        except Exception as e:
                            self._log(f"⚠️ Ошибка чтения результата: {e}")
                            self._cleanup_temp(result_path)
                    else:
                        code = proc.returncode or 0
                        exit_hex = f"0x{code & 0xFFFFFFFF:08X}"
                        self._log(f"⚠️ Подпроцесс упал (exit: {exit_hex})")
                        if stderr:
                            self._log(f"📋 {stderr[-500:]}")
                        self._cleanup_temp(result_path)

            # Все subprocess-попытки провалились
            raise RuntimeError(
                "Не удалось выполнить транскрипцию ни с одним VAD-методом. "
                "Возможная причина: несовместимость с виртуальной средой (Parallels/VM)."
            )

        # ---- DIRECT MODE (frozen exe / script not found) ----
        self._log("⚙️ Прямая транскрипция (без изоляции подпроцессом)...")
        for device, compute_type in devices_to_try:
            if device != self.device:
                self._log(f"⚠️ Повторная попытка на CPU...")

            old_limit = getattr(sys, "getrecursionlimit", lambda: 1000)()
            try:
                sys.setrecursionlimit(4000)
                model = whisperx.load_model(
                    self.model_size,
                    device=device,
                    compute_type=compute_type,
                    language=language,
                    download_root=download_root or None,
                    vad_method="pyannote",
                )
            except Exception as e:
                self._log(f"⚠️ Ошибка загрузки модели на {device}: {e}")
                self._cleanup_memory()
                continue
            finally:
                try:
                    sys.setrecursionlimit(old_limit)
                except Exception:
                    pass

            if self.should_stop_callback and self.should_stop_callback():
                del model
                self._cleanup_memory()
                raise InterruptedError("Processing stopped by user")

            self._log(f"⚙️ Параметры: device={device}, compute_type={compute_type}, "
                       f"batch_size={batch_size}, chunk_size=10")
            try:
                result = model.transcribe(audio_path, batch_size=batch_size, chunk_size=10)
                self.device = device
                self.compute_type = compute_type
                return model, result
            except Exception as e:
                self._log(f"⚠️ Ошибка транскрипции на {device}: {e}")
                try:
                    del model
                except Exception:
                    pass
                self._cleanup_memory()

        raise RuntimeError("Не удалось выполнить транскрипцию ни на одном устройстве.")

    def _cleanup_temp(self, *paths):
        """Удаляет временные файлы, игнорируя ошибки."""
        for p in paths:
            try:
                if p and os.path.exists(p):
                    os.unlink(p)
            except OSError:
                pass

    def transcribe_full(
        self,
        audio_path: str,
        language: Optional[str] = None,
        batch_size: int = 4,  # Уменьшено для стабильности с float32 на Mac
        min_speakers: Optional[int] = None,
        max_speakers: Optional[int] = None,
        num_speakers: Optional[int] = None
    ) -> Dict:
        """
        Запуск полного пайплайна.
        """
        # На Windows длинные пути (>260 символов) требуют префикса \\?\ для os.path.exists и открытия файла
        resolved_path = resolve_path_for_win(audio_path)
        if not os.path.exists(resolved_path):
            raise FileNotFoundError(f"Файл не найден: {audio_path}")

        # Импортируем внутри метода, чтобы не грузить память при старте приложения
        import whisperx

        try:
            # Проверяем флаг остановки перед началом
            if self.should_stop_callback and self.should_stop_callback():
                self._log("⏹️ Транскрипция прервана пользователем")
                raise InterruptedError("Processing stopped by user")
            
            # --- ШАГ 1: ТРАНСКРИПЦИЯ ---
            self._log(f"\n🎧 Шаг 1/4: Транскрипция ({self.model_size})...")

            download_root = str(APP_PATHS.get("models", ""))

            # Проверяем, есть ли модель в кэше
            if self._is_model_cached(download_root):
                self._log("✅ Модель найдена в кэше, загрузка из локальных файлов...")
            else:
                self._log(f"📥 Модель {self.model_size} не найдена локально. Начинается скачивание...")
                self._log("⏳ Это нужно сделать только один раз. Скачивание может занять 5–15 минут.")
                self._download_model_with_progress(download_root)

            # Диаризация строго через Pyannote: VAD при загрузке тоже Pyannote.
            # В упакованном билде (PyInstaller) pytorch_lightning + speechbrain вызывают
            # глубокую рекурсию в inspect.stack() → RecursionError. Временно повышаем лимит.
            self._log("⚙️ Инициализация модели...")

            model, result = self._load_and_transcribe(
                whisperx, resolved_path, language, download_root, batch_size
            )
            
            # Проверяем флаг остановки после транскрипции
            if self.should_stop_callback and self.should_stop_callback():
                del model
                self._cleanup_memory()
                self._log("⏹️ Транскрипция прервана пользователем")
                raise InterruptedError("Processing stopped by user")
            
            detected_lang = result["language"]
            self._log(f"🌍 Язык оригинала: {detected_lang}")
            
            # Чистим память
            del model
            self._cleanup_memory()
            
            # Проверяем флаг остановки перед alignment
            if self.should_stop_callback and self.should_stop_callback():
                self._log("⏹️ Обработка прервана пользователем")
                raise InterruptedError("Processing stopped by user")
            
            # --- ШАГ 2: ВЫРАВНИВАНИЕ (Alignment) ---
            self._log(f"\n📐 Шаг 2/4: Выравнивание таймингов...")
            
            # Нормализуем код языка для alignment
            align_lang = self._normalize_language_code(detected_lang)
            self._log(f"🔤 Код языка для alignment: {align_lang} (исходный: {detected_lang})")
            
            alignment_success = False
            try:
                self._log(f"📦 Загрузка модели выравнивания для языка: {align_lang}...")
                align_model, align_metadata = whisperx.load_align_model(
                    language_code=align_lang,
                    device=self.device
                )
                self._log(f"✅ Модель выравнивания загружена")
                
                self._log(f"🔄 Запуск выравнивания...")
                result = whisperx.align(
                    result["segments"],
                    align_model,
                    align_metadata,
                    resolved_path,
                    device=self.device,
                    return_char_alignments=False
                )
                
                # КРИТИЧЕСКОЕ ОТЛАДОЧНОЕ ЛОГИРОВАНИЕ
                if result.get("segments") and len(result["segments"]) > 0:
                    seg0 = result["segments"][0]
                    print(f"\n{'='*60}")
                    print(f"DEBUG SEGMENT 0 KEYS: {list(seg0.keys())}")
                    print(f"DEBUG SEGMENT 0 HAS 'words': {'words' in seg0}")
                    if "words" in seg0:
                        words_count = len(seg0.get("words", []))
                        print(f"DEBUG SEGMENT 0 WORDS COUNT: {words_count}")
                        if words_count > 0:
                            first_word = seg0["words"][0]
                            print(f"DEBUG FIRST WORD: {first_word}")
                            print(f"DEBUG FIRST WORD KEYS: {list(first_word.keys())}")
                        else:
                            print(f"DEBUG SEGMENT 0 WORDS: [] (пустой список)")
                    else:
                        print(f"DEBUG SEGMENT 0 WORDS: NO_WORDS_FOUND")
                    print(f"DEBUG SEGMENT 0 TEXT: {seg0.get('text', 'NO_TEXT')[:100]}")
                    print(f"DEBUG SEGMENT 0 TIME: {seg0.get('start', 'NO_START')} -> {seg0.get('end', 'NO_END')}")
                    print(f"{'='*60}\n")
                    
                    # Проверяем все сегменты
                    segments_with_words = sum(1 for s in result["segments"] if "words" in s and len(s.get("words", [])) > 0)
                    total_segments = len(result["segments"])
                    self._log(f"📊 Статистика выравнивания: {segments_with_words}/{total_segments} сегментов содержат слова")
                    
                    if segments_with_words == 0:
                        self._log(f"⚠️ ВНИМАНИЕ: Выравнивание не добавило слова ни в один сегмент!")
                        self._log(f"⚠️ Это означает, что alignment не сработал. Проверьте код языка и модель.")
                else:
                    print(f"\n{'='*60}")
                    print(f"DEBUG: result['segments'] пуст или отсутствует!")
                    print(f"DEBUG result keys: {list(result.keys())}")
                    print(f"{'='*60}\n")
                
                alignment_success = True
                del align_model
                del align_metadata
                
            except FileNotFoundError as e:
                self._log(f"❌ Ошибка: Модель выравнивания для языка '{align_lang}' не найдена")
                self._log(f"💡 Попробуйте другой язык или проверьте доступность моделей")
                self._log(f"📋 Детали: {str(e)}")
            except MemoryError as e:
                self._log(f"❌ Ошибка памяти при выравнивании: {e}")
                self._log(f"💡 Попробуйте уменьшить batch_size или использовать меньшую модель")
            except Exception as e:
                error_type = type(e).__name__
                self._log(f"❌ Ошибка выравнивания ({error_type}): {str(e)}")
                self._log(f"📋 Traceback:")
                self._log(traceback.format_exc())
                self._log(f"⚠️ Продолжаем без выравнивания (тайминги могут быть неточными)")
            
            if not alignment_success:
                self._log(f"⚠️ Выравнивание пропущено. Сегментация будет базовая (без разбиения на предложения)")
            
            self._cleanup_memory()
            
            # Проверяем флаг остановки перед диаризацией
            if self.should_stop_callback and self.should_stop_callback():
                self._log("⏹️ Обработка прервана пользователем")
                raise InterruptedError("Processing stopped by user")
            
            # --- ШАГ 3: ДИАРИЗАЦИЯ ---
            self._log(f"\n👥 Шаг 3/4: Диаризация (PyAnnote)...")
            
            diarize_segments = None
            if self.hf_token:
                from whisperx import diarize
                
                # Загружаем пайплайн диаризации
                diarize_model = diarize.DiarizationPipeline(
                    use_auth_token=self.hf_token,
                    device=self.device
                )
                
                diarize_segments = diarize_model(
                    resolved_path,
                    min_speakers=min_speakers,
                    max_speakers=max_speakers,
                    num_speakers=num_speakers
                )
                
                del diarize_model
                self._cleanup_memory()
                
                # --- ШАГ 4: СБОРКА И УМНАЯ НАРЕЗКА ---
                self._log(f"\n🔗 Шаг 4/4: Сборка и разбиение на предложения...")
                
                # Присваиваем спикеров словам
                result = diarize.assign_word_speakers(
                    diarize_segments,
                    result
                )
            else:
                self._log("⚠️ HF Token не найден. Диаризация пропущена (будет только текст).")

            # Запускаем РАЗБИЕНИЕ ПО ПРЕДЛОЖЕНИЯМ (Sentence-Level Splitter)
            # Реконструирует сегменты строго по предложениям на уровне слов
            # Это позволяет LLM видеть переходы между спикерами даже в быстром диалоге
            # LLM в corrector.py выступит "Script Editor" и исправит спикеров по контексту
            final_segments = self._smart_sentence_split(result["segments"])
            
            # Подсчет статистики
            speakers_found = set(s.get("speaker") for s in final_segments if "speaker" in s)
            self._log(f"✅ Готово! Спикеров: {len(speakers_found)}. Сегментов: {len(final_segments)}")
            
            return {
                "segments": final_segments,
                "language": detected_lang
            }

        except InterruptedError:
            # Обработка прерывания пользователем
            self._log("⏹️ Транскрипция прервана пользователем")
            return {"segments": [], "language": "en", "stopped": True}
        except Exception as e:
            self._log(f"❌ ОШИБКА: {e}")
            self._log(traceback.format_exc())
            # Возвращаем пустой результат, чтобы программа не упала полностью
            return {"segments": [], "language": "en", "error": str(e)}
        finally:
            self._cleanup_memory()

    def _smart_sentence_split(self, whisperx_segments: List[Dict]) -> List[Dict]:
        """
        РАЗБИЕНИЕ ПО ПРЕДЛОЖЕНИЯМ (Sentence-Level Splitter)
        
        Реконструирует сегменты строго по предложениям на уровне слов.
        Это позволяет LLM видеть переходы между спикерами даже в быстром диалоге.
        
        Алгоритм:
        1. FLATTEN: Извлекает ВСЕ слова из ВСЕХ сегментов в плоский список
        2. RECONSTRUCT: Итерируется по словам и строит сегменты по предложениям
        3. TRIGGER SPLIT: Разбивает при пунктуации [.!?]
        4. SAFETY SPLIT: Разбивает если предложение > 3.0 сек без пунктуации
        5. GAP SPLIT: Разбивает при паузе > 0.5 сек между словами
        
        Результат: Много коротких сегментов на уровне предложений
        Пример: [20s-24s]: "...ID." и [24s-25s]: "Yes." вместо одного блока
        """
        # ОТЛАДКА: Проверяем входные данные
        print(f"\n{'='*60}")
        print(f"DEBUG Sentence Splitter: получено {len(whisperx_segments)} сегментов")
        if whisperx_segments:
            print(f"DEBUG Первый сегмент keys: {list(whisperx_segments[0].keys())}")
            if "words" in whisperx_segments[0]:
                words_in_first = len(whisperx_segments[0].get("words", []))
                print(f"DEBUG Слов в первом сегменте: {words_in_first}")
        print(f"{'='*60}\n")
        
        # ШАГ 1: FLATTEN - Извлекаем ВСЕ слова из ВСЕХ сегментов в плоский список
        all_words = []
        segments_with_words = 0
        
        for seg in whisperx_segments:
            if "words" in seg and seg["words"]:
                segments_with_words += 1
                seg_speaker = seg.get("speaker", "SPEAKER_UNKNOWN")
                
                for word in seg["words"]:
                    # Проверяем, что слово имеет необходимые поля
                    if not isinstance(word, dict) or "word" not in word:
                        continue
                    
                    # Наследуем спикера сегмента, если у слова нет своего
                    if "speaker" not in word:
                        word["speaker"] = seg_speaker
                    
                    # Убеждаемся, что есть временные метки (безопасность: float())
                    word_start = word.get("start")
                    word_end = word.get("end")
                    
                    if word_start is None or word_end is None:
                        # Пробуем взять из сегмента
                        word_start = float(seg.get("start", 0)) if "start" in seg else 0.0
                        word_end = float(seg.get("end", 0)) if "end" in seg else 0.0
                    
                    word["start"] = float(word_start)
                    word["end"] = float(word_end)
                    
                    all_words.append(word)
        
        print(f"DEBUG: Собрано {len(all_words)} слов из {segments_with_words} сегментов")
        
        # Если слов нет (Alignment не сработал), возвращаем как есть
        if not all_words:
            self._log(f"⚠️ Разбиение по предложениям невозможно: слова не найдены (alignment не сработал)")
            self._log(f"📝 Используется базовая сегментация")
            return self._format_basic(whisperx_segments)

        # ШАГ 2: RECONSTRUCT - Итерируемся по словам и строим сегменты по предложениям
        reconstructed_segments = []
        current_words = []
        current_speaker = None
        sentence_start_time = None  # Время начала текущего предложения
        
        for i, word in enumerate(all_words):
            text = word.get("word", "").strip()
            if not text:
                continue
            
            speaker = word.get("speaker", "SPEAKER_UNKNOWN")
            word_start = float(word.get("start", 0))
            word_end = float(word.get("end", 0))
            
            # Инициализация: первое слово
            if sentence_start_time is None:
                sentence_start_time = word_start
                current_speaker = speaker
            
            # Смена спикера = принудительный разрыв (новое предложение)
            if speaker != current_speaker:
                if current_words:
                    reconstructed_segments.append(self._make_segment(current_words, current_speaker))
                    current_words = []
                    sentence_start_time = word_start
                current_speaker = speaker
            
            # Добавляем слово в текущее предложение
            current_words.append(word)
            
            # ПРОВЕРКА УСЛОВИЙ РАЗБИЕНИЯ (TRIGGER SPLIT):
            should_split = False
            
            # Проверяем следующее слово для контекста (для GAP SPLIT)
            has_next_word = i < len(all_words) - 1
            
            # Условие 1: Пунктуация [.!?] - АГРЕССИВНОЕ РАЗБИЕНИЕ
            # ВСЕГДА разбиваем при пунктуации, даже если следующее слово - число
            # Лучше "over-split" (LLM сможет объединить), чем "under-split" (заблокирует разных спикеров)
            has_punctuation = bool(re.search(r'[.!?]+$', text))
            if has_punctuation:
                should_split = True  # ВСЕГДА разбиваем при пунктуации
            
            # Условие 2: SAFETY SPLIT - Длительность предложения > 4.0 сек без пунктуации
            if not should_split and sentence_start_time is not None:
                sentence_duration = word_end - sentence_start_time
                if sentence_duration > 4.0:
                    should_split = True
            
            # Условие 3: GAP SPLIT - Пауза между словами > 0.8 сек
            if not should_split and has_next_word:
                next_word = all_words[i + 1]
                next_start = float(next_word.get("start", word_end))
                pause_duration = next_start - word_end
                if pause_duration > 0.8:
                    should_split = True
            
            # Выполняем разбиение (завершаем предложение)
            if should_split:
                if current_words:
                    reconstructed_segments.append(self._make_segment(current_words, current_speaker))
                    current_words = []
                    # Сброс для следующего предложения
                    if i < len(all_words) - 1:
                        next_word = all_words[i + 1]
                        sentence_start_time = float(next_word.get("start", word_end))
                    else:
                        sentence_start_time = None

        # Добавляем хвост (последнее предложение)
        if current_words:
            reconstructed_segments.append(self._make_segment(current_words, current_speaker))
        
        self._log(f"📝 Разбиение по предложениям: {len(whisperx_segments)} → {len(reconstructed_segments)} сегментов")
        
        return reconstructed_segments

    def _make_segment(self, words: List[Dict], speaker: str) -> Dict:
        """Собирает сегмент из списка слов (безопасность: float())"""
        if not words: 
            return {}
        
        start = float(words[0].get("start", 0))
        end = float(words[-1].get("end", 0))
        text = " ".join([w.get("word", "").strip() for w in words])
        
        return {
            "start": start,
            "end": end,
            "text": text.replace("  ", " ").strip(),
            "speaker": speaker
        }

    def _format_basic(self, segments: List[Dict]) -> List[Dict]:
        """Fallback метод, если нет детальных слов (безопасность: float())"""
        clean = []
        for seg in segments:
            clean.append({
                "start": float(seg.get("start", 0)),
                "end": float(seg.get("end", 0)),
                "text": seg.get("text", "").strip(),
                "speaker": seg.get("speaker", "SPEAKER_UNKNOWN")
            })
        return clean
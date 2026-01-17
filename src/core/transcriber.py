# -*- coding: utf-8 -*-
import os
import sys
from pathlib import Path
from typing import Optional, Callable
from faster_whisper import WhisperModel
import platform

# Импортируем пути из config
from core.config import APP_PATHS

def get_models_path():
    """
    Возвращает путь к папке с моделями.
    Для exe: рядом с exe файлом в папке models/
    Для исходников: в папке приложения models/
    """
    if getattr(sys, 'frozen', False):
        # Если запущено из exe файла
        if platform.system() == 'Windows':
            # На Windows exe находится в sys.executable
            exe_dir = Path(sys.executable).parent
        else:
            # На macOS/Linux
            exe_dir = Path(sys.executable).parent
        models_dir = exe_dir / "models"
    else:
        # Если запущено из исходников
        base_dir = Path(__file__).parent.parent.parent
        models_dir = base_dir / "models"
    
    # Создаем папку, если её нет
    models_dir.mkdir(parents=True, exist_ok=True)
    return models_dir

class Transcriber:
    """
    Класс для транскрипции аудио/видео файлов с использованием faster-whisper.
    Работает в неблокирующем режиме для NiceGUI.
    """
    
    def __init__(self, model_size: str = "base", device: str = "auto", progress_callback: Optional[Callable[[str], None]] = None):
        """
        Инициализация транскрибера.
        
        Args:
            model_size: Размер модели ("tiny", "base", "small", "medium", "large-v2", "large-v3")
            device: Устройство для обработки ("cpu", "cuda", "auto")
            progress_callback: Функция для отчета о прогрессе (принимает строку сообщения)
        """
        self.model_size = model_size
        self.device = device
        self.progress_callback = progress_callback
        self.model: Optional[WhisperModel] = None
        self.models_path = get_models_path()
        
    def _log(self, message: str):
        """Вспомогательный метод для логирования"""
        if self.progress_callback:
            self.progress_callback(message)
    
    def _load_model(self):
        """Загружает модель Whisper. Скачивает автоматически, если не найдена."""
        if self.model is not None:
            return
        
        self._log(f"📦 Загрузка модели Whisper ({self.model_size})...")
        
        # Определяем путь к модели
        # faster-whisper автоматически скачает модель в кэш, если указать download_root
        download_root = str(self.models_path)
        
        # Определяем compute_type в зависимости от устройства
        # Для CPU используем int8, для GPU пробуем float16, если не поддерживается - int8_float16
        compute_type = "int8"  # Безопасное значение по умолчанию для CPU
        
        if self.device == "auto":
            # Пытаемся определить устройство автоматически
            try:
                import torch
                if torch.cuda.is_available():
                    self.device = "cuda"
                    compute_type = "float16"  # Для CUDA пробуем float16
                else:
                    self.device = "cpu"
                    compute_type = "int8"
            except ImportError:
                # Если torch не установлен, используем CPU
                self.device = "cpu"
                compute_type = "int8"
        elif self.device == "cuda":
            compute_type = "float16"
        else:
            compute_type = "int8"
        
        try:
            # Пробуем загрузить с оптимальным compute_type
            self.model = WhisperModel(
                self.model_size,
                device=self.device,
                download_root=download_root,
                compute_type=compute_type
            )
            self._log(f"✅ Модель загружена: {self.model_size} (устройство: {self.device}, тип: {compute_type})")
        except Exception as e:
            # Если float16 не поддерживается, пробуем int8_float16 или int8
            if "float16" in str(e).lower():
                self._log(f"⚠️ Float16 не поддерживается, пробуем int8...")
                try:
                    compute_type = "int8_float16" if self.device != "cpu" else "int8"
                    self.model = WhisperModel(
                        self.model_size,
                        device=self.device,
                        download_root=download_root,
                        compute_type=compute_type
                    )
                    self._log(f"✅ Модель загружена: {self.model_size} (устройство: {self.device}, тип: {compute_type})")
                except Exception as e2:
                    # Последняя попытка - только int8
                    if compute_type != "int8":
                        self._log(f"⚠️ Пробуем int8...")
                        self.model = WhisperModel(
                            self.model_size,
                            device="cpu",  # Принудительно CPU с int8
                            download_root=download_root,
                            compute_type="int8"
                        )
                        self._log(f"✅ Модель загружена: {self.model_size} (устройство: cpu, тип: int8)")
                    else:
                        self._log(f"❌ Ошибка загрузки модели: {str(e2)}")
                        raise
            else:
                self._log(f"❌ Ошибка загрузки модели: {str(e)}")
                raise
    
    def transcribe(
        self,
        audio_path: str,
        language: Optional[str] = None,
        task: str = "transcribe",
        beam_size: int = 5,
        best_of: int = 5,
        patience: float = 1.0,
        length_penalty: float = 1.0,
        temperature: float = 0.0,
        compression_ratio_threshold: float = 2.4,
        log_prob_threshold: float = -1.0,
        no_speech_threshold: float = 0.6,
        condition_on_previous_text: bool = True,
        initial_prompt: Optional[str] = None,
        word_timestamps: bool = True,
        prepend_punctuations: str = """\"'¿([{-""",
        append_punctuations: str = """\"'.。,，!！?？:：")]}、""",
        vad_filter: bool = True,
        vad_parameters: Optional[dict] = None
    ) -> dict:
        """
        Транскрибирует аудио/видео файл.
        
        Args:
            audio_path: Путь к аудио/видео файлу
            language: Язык (код ISO 639-1, например "ru", "en"). Если None, определяется автоматически
            task: "transcribe" или "translate"
            word_timestamps: Включать ли временные метки для каждого слова
            vad_filter: Использовать ли фильтрацию голосовой активности
            ... остальные параметры модели
            
        Returns:
            Словарь с результатами:
            {
                "text": полный текст транскрипции,
                "segments": список сегментов с временными метками,
                "language": определенный язык,
                "language_probability": вероятность определения языка
            }
        """
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Файл не найден: {audio_path}")
        
        # Загружаем модель, если еще не загружена
        self._load_model()
        
        self._log(f"🎤 Начало транскрипции: {os.path.basename(audio_path)}")
        
        try:
            # Выполняем транскрипцию
            segments, info = self.model.transcribe(
                audio_path,
                language=language,
                task=task,
                beam_size=beam_size,
                best_of=best_of,
                patience=patience,
                length_penalty=length_penalty,
                temperature=temperature,
                compression_ratio_threshold=compression_ratio_threshold,
                log_prob_threshold=log_prob_threshold,
                no_speech_threshold=no_speech_threshold,
                condition_on_previous_text=condition_on_previous_text,
                initial_prompt=initial_prompt,
                word_timestamps=word_timestamps,
                prepend_punctuations=prepend_punctuations,
                append_punctuations=append_punctuations,
                vad_filter=vad_filter,
                vad_parameters=vad_parameters
            )
            
            self._log(f"🌍 Определенный язык: {info.language} (вероятность: {info.language_probability:.2%})")
            
            # Собираем результаты
            full_text = ""
            segments_list = []
            
            segment_count = 0
            for segment in segments:
                segment_count += 1
                segment_text = segment.text.strip()
                full_text += segment_text + " "
                
                segment_data = {
                    "id": segment.id,
                    "start": segment.start,
                    "end": segment.end,
                    "text": segment_text,
                    "words": []
                }
                
                # Добавляем слова с временными метками, если доступны
                if word_timestamps and hasattr(segment, 'words') and segment.words:
                    for word in segment.words:
                        segment_data["words"].append({
                            "word": word.word,
                            "start": word.start,
                            "end": word.end,
                            "probability": word.probability
                        })
                
                segments_list.append(segment_data)
                
                # Отчет о прогрессе каждые 10 сегментов
                if segment_count % 10 == 0:
                    self._log(f"📝 Обработано сегментов: {segment_count}...")
            
            self._log(f"✅ Транскрипция завершена! Всего сегментов: {segment_count}")
            
            return {
                "text": full_text.strip(),
                "segments": segments_list,
                "language": info.language,
                "language_probability": info.language_probability
            }
            
        except Exception as e:
            self._log(f"❌ Ошибка транскрипции: {str(e)}")
            raise


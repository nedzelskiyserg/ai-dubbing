# -*- coding: utf-8 -*-
"""
Модуль диаризации (разделение спикеров) с использованием PyAnnote Audio.
Интегрируется с транскрипцией для связки "Текст -> Кто сказал".
"""
import os
import sys
from pathlib import Path
from typing import Optional, Callable, List, Dict, Tuple
import platform

# Логируем информацию о Python окружении для диагностики
def _log_import_info():
    """Логирует информацию о Python окружении для диагностики"""
    info = {
        'python_executable': sys.executable,
        'python_version': sys.version,
        'sys.path': sys.path[:5],  # Первые 5 путей
        'frozen': getattr(sys, 'frozen', False),
    }
    return info

try:
    from pyannote.audio import Pipeline
    from pyannote.core import Annotation, Segment
    PYANNOTE_AVAILABLE = True
    IMPORT_ERROR = None
except ImportError as e:
    PYANNOTE_AVAILABLE = False
    Pipeline = None
    Annotation = None
    Segment = None
    IMPORT_ERROR = str(e)
    # Сохраняем информацию об окружении при ошибке
    IMPORT_ENV_INFO = _log_import_info()

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
            exe_dir = Path(sys.executable).parent
        else:
            exe_dir = Path(sys.executable).parent
        models_dir = exe_dir / "models"
    else:
        # Если запущено из исходников
        base_dir = Path(__file__).parent.parent.parent
        models_dir = base_dir / "models"
    
    models_dir.mkdir(parents=True, exist_ok=True)
    return models_dir


class Diarizer:
    """
    Класс для диаризации аудио/видео файлов с использованием PyAnnote Audio.
    Определяет, кто и когда говорил (разделение спикеров).
    """
    
    def __init__(
        self,
        hf_token: Optional[str] = None,
        progress_callback: Optional[Callable[[str], None]] = None
    ):
        """
        Инициализация диаризатора.
        
        Args:
            hf_token: Hugging Face token для доступа к моделям (опционально)
            progress_callback: Функция для отчета о прогрессе
        """
        if not PYANNOTE_AVAILABLE:
            error_msg = "pyannote.audio не установлен. Установите: pip install pyannote.audio"
            if 'IMPORT_ERROR' in globals():
                error_msg += f"\nДетали ошибки: {IMPORT_ERROR}"
            raise ImportError(error_msg)
        
        self.hf_token = hf_token
        self.progress_callback = progress_callback
        self.pipeline: Optional[Pipeline] = None
        self.models_path = get_models_path()
        
    def _log(self, message: str):
        """Вспомогательный метод для логирования"""
        if self.progress_callback:
            self.progress_callback(message)
    
    def _load_pipeline(self):
        """Загружает pipeline для диаризации"""
        if self.pipeline is not None:
            return
        
        self._log("📦 Загрузка модели диаризации PyAnnote...")
        
        try:
            # Используем предобученную модель для диаризации
            # Модель автоматически скачается при первом использовании
            model_name = "pyannote/speaker-diarization-3.1"
            
            # Если есть токен, используем его
            if self.hf_token:
                self.pipeline = Pipeline.from_pretrained(
                    model_name,
                    use_auth_token=self.hf_token,
                    cache_dir=str(self.models_path)
                )
            else:
                # Пробуем загрузить без токена (для публичных моделей)
                try:
                    self.pipeline = Pipeline.from_pretrained(
                        model_name,
                        cache_dir=str(self.models_path)
                    )
                except Exception as e:
                    self._log(f"⚠️ Не удалось загрузить модель без токена: {e}")
                    self._log("💡 Для использования диаризации нужен Hugging Face token")
                    self._log("   Получите токен на https://huggingface.co/settings/tokens")
                    raise
            
            # Перемещаем pipeline на GPU, если доступно
            import torch
            if torch.cuda.is_available():
                self.pipeline.to(torch.device("cuda"))
                self._log("✅ Модель загружена на GPU")
            else:
                self._log("✅ Модель загружена на CPU")
                
        except Exception as e:
            self._log(f"❌ Ошибка загрузки модели: {str(e)}")
            raise
    
    def diarize(
        self,
        audio_path: str,
        num_speakers: Optional[int] = None,
        min_speakers: Optional[int] = None,
        max_speakers: Optional[int] = None
    ) -> Dict:
        """
        Выполняет диаризацию аудио/видео файла.
        
        Args:
            audio_path: Путь к аудио/видео файлу
            num_speakers: Точное количество спикеров (если известно)
            min_speakers: Минимальное количество спикеров
            max_speakers: Максимальное количество спикеров
        
        Returns:
            Словарь с результатами диаризации:
            {
                "segments": список сегментов с информацией о спикере,
                "speakers": список уникальных спикеров,
                "total_duration": общая длительность
            }
        """
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Файл не найден: {audio_path}")
        
        # Загружаем pipeline, если еще не загружен
        self._load_pipeline()
        
        self._log(f"🎙️ Начало диаризации: {os.path.basename(audio_path)}")
        
        try:
            # Выполняем диаризацию
            diarization = self.pipeline(
                audio_path,
                num_speakers=num_speakers,
                min_speakers=min_speakers,
                max_speakers=max_speakers
            )
            
            # Преобразуем результаты в удобный формат
            segments = []
            speakers = set()
            
            for turn, _, speaker in diarization.itertracks(yield_label=True):
                segments.append({
                    "start": turn.start,
                    "end": turn.end,
                    "speaker": speaker,
                    "duration": turn.end - turn.start
                })
                speakers.add(speaker)
            
            speakers_list = sorted(list(speakers))
            
            self._log(f"✅ Диаризация завершена!")
            self._log(f"👥 Найдено спикеров: {len(speakers_list)}")
            self._log(f"📊 Всего сегментов: {len(segments)}")
            
            return {
                "segments": segments,
                "speakers": speakers_list,
                "total_duration": diarization.get_timeline().extent().end if segments else 0.0
            }
            
        except Exception as e:
            self._log(f"❌ Ошибка диаризации: {str(e)}")
            raise


def merge_transcription_with_diarization(
    transcription_segments: List[Dict],
    diarization_segments: List[Dict]
) -> List[Dict]:
    """
    Связывает сегменты транскрипции с результатами диаризации.
    Определяет, какой спикер сказал каждый сегмент текста.
    
    Args:
        transcription_segments: Сегменты из транскрипции (с временными метками)
        diarization_segments: Сегменты из диаризации (с информацией о спикере)
    
    Returns:
        Список сегментов с объединенной информацией (текст + спикер)
    """
    merged_segments = []
    
    for trans_seg in transcription_segments:
        trans_start = trans_seg.get("start", 0)
        trans_end = trans_seg.get("end", trans_start)
        trans_mid = (trans_start + trans_end) / 2
        
        # Находим спикера для этого временного интервала
        speaker = None
        max_overlap = 0
        
        for diar_seg in diarization_segments:
            diar_start = diar_seg.get("start", 0)
            diar_end = diar_seg.get("end", diar_start)
            
            # Вычисляем пересечение временных интервалов
            overlap_start = max(trans_start, diar_start)
            overlap_end = min(trans_end, diar_end)
            overlap = max(0, overlap_end - overlap_start)
            
            if overlap > max_overlap:
                max_overlap = overlap
                speaker = diar_seg.get("speaker")
        
        # Создаем объединенный сегмент
        merged_seg = trans_seg.copy()
        merged_seg["speaker"] = speaker if speaker else "UNKNOWN"
        merged_segments.append(merged_seg)
    
    return merged_segments


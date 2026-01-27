#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вспомогательный скрипт для генерации TTS через venv_tts.
Запускается в отдельном процессе с Python 3.11+ из venv_tts.
"""
import sys
import json
import os
import time

# Устанавливаем переменную окружения для лицензии
os.environ["COQUI_TOS_AGREED"] = "1"

# Глобальная переменная для кэширования модели
_cached_tts = None
_cached_model_name = None

def generate_tts(text: str, speaker_wav: str, output_path: str, language: str = "ru", model_name: str = "tts_models/multilingual/multi-dataset/xtts_v2", segment_info: dict = None):
    """
    Генерирует аудио с помощью TTS.
    
    Args:
        text: Текст для генерации
        speaker_wav: Путь к референсному аудио
        output_path: Путь для сохранения результата
        language: Язык генерации
        model_name: Название модели TTS
        segment_info: Информация о сегменте для логирования {"index": int, "total": int}
        
    Returns:
        dict с результатом: {"success": bool, "error": str или None, "load_time": float, "gen_time": float}
    """
    global _cached_tts, _cached_model_name
    
    load_start = time.time()
    
    try:
        from TTS.api import TTS
        
        # Кэшируем модель, если она еще не загружена или изменилась
        if _cached_tts is None or _cached_model_name != model_name:
            if segment_info:
                print(f"📦 [{segment_info['index']}/{segment_info['total']}] Загрузка модели XTTS...", file=sys.stderr, flush=True)
            else:
                print("📦 Загрузка модели XTTS...", file=sys.stderr, flush=True)
            
            _cached_tts = TTS(model_name=model_name, progress_bar=False)
            _cached_model_name = model_name
            
            load_time = time.time() - load_start
            if segment_info:
                print(f"✅ [{segment_info['index']}/{segment_info['total']}] Модель загружена за {load_time:.1f}с", file=sys.stderr, flush=True)
            else:
                print(f"✅ Модель загружена за {load_time:.1f}с", file=sys.stderr, flush=True)
        else:
            load_time = 0.0  # Модель уже загружена
        
        # Генерируем аудио
        gen_start = time.time()
        
        if segment_info:
            print(f"🎤 [{segment_info['index']}/{segment_info['total']}] Генерация аудио ({len(text)} символов)...", file=sys.stderr, flush=True)
        
        _cached_tts.tts_to_file(
            text=text,
            speaker_wav=speaker_wav,
            language=language,
            file_path=output_path,
            split_sentences=False
        )
        
        gen_time = time.time() - gen_start
        
        if segment_info:
            print(f"✅ [{segment_info['index']}/{segment_info['total']}] Аудио сгенерировано за {gen_time:.1f}с", file=sys.stderr, flush=True)
        
        return {
            "success": True, 
            "error": None,
            "load_time": load_time,
            "gen_time": gen_time
        }
    except Exception as e:
        return {
            "success": False, 
            "error": str(e),
            "load_time": time.time() - load_start,
            "gen_time": 0.0
        }


if __name__ == "__main__":
    # Читаем аргументы из stdin (JSON)
    input_data = json.loads(sys.stdin.read())
    
    result = generate_tts(
        text=input_data["text"],
        speaker_wav=input_data["speaker_wav"],
        output_path=input_data["output_path"],
        language=input_data.get("language", "ru"),
        model_name=input_data.get("model_name", "tts_models/multilingual/multi-dataset/xtts_v2"),
        segment_info=input_data.get("segment_info")
    )
    
    # Выводим результат в stdout (JSON)
    print(json.dumps(result))

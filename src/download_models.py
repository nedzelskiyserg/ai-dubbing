# -*- coding: utf-8 -*-
"""
Предзагрузка модели Whisper large-v3 в папку приложения (установка/первый запуск).
Скачивает только faster-whisper large-v3 через Hugging Face Hub, без загрузки Pyannote в память,
чтобы избежать краша 0xC0000005 во время установки.
Запуск: из корня src/ тем же Python, что и api_server (cwd=src, python download_models.py).
"""
import os
import sys

# Добавляем текущую папку в path (как при запуске api_server)
if __name__ == "__main__":
    src_dir = os.path.dirname(os.path.abspath(__file__))
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)

from core.config import APP_PATHS

MODEL_ID = "Systran/faster-whisper-large-v3"


def main():
    cache_dir = APP_PATHS["models"]
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_str = str(cache_dir)

    # Указываем Hugging Face кэшировать в папку приложения
    os.environ["HUGGINGFACE_HUB_CACHE"] = cache_str
    os.environ.setdefault("HF_HOME", cache_str)

    print(f"[download_models] Кэш моделей: {cache_str}")
    print(f"[download_models] Скачивание {MODEL_ID} (~3 ГБ)...")

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("[download_models] huggingface_hub не найден, пропуск предзагрузки.")
        return 0

    try:
        snapshot_download(
            MODEL_ID,
            cache_dir=cache_str,
            local_dir=None,  # использовать кэш как обычно
            local_dir_use_symlinks=False,
        )
        print("[download_models] Модель large-v3 успешно загружена.")
        return 0
    except Exception as e:
        print(f"[download_models] Ошибка загрузки: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

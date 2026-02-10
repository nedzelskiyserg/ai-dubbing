# -*- coding: utf-8 -*-
"""
Предзагрузка модели XTTS v2 в папку приложения (установка/первый запуск).
Скачивает модель через Coqui TTS с retry при сетевых ошибках.
Запуск: python download_tts_model.py
"""
import os
import sys
import time
import shutil
import ssl

# Добавляем текущую папку в path
if __name__ == "__main__":
    src_dir = os.path.dirname(os.path.abspath(__file__))
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)

# Фикс SSL для embedded Python (до любых сетевых вызовов)
try:
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
    ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())
except ImportError:
    if sys.platform == 'win32':
        ssl._create_default_https_context = ssl._create_unverified_context
except Exception:
    pass

from core.config import APP_PATHS

MODEL_NAME = "tts_models/multilingual/multi-dataset/xtts_v2"
MAX_RETRIES = 5
# Модель ~1.8 ГБ, директория в кэше
MODEL_DIR_NAME = "tts_models--multilingual--multi-dataset--xtts_v2"


def _get_tts_cache_dir():
    """Возвращает директорию кэша TTS (та же что используется в voice_cloner)."""
    return str(APP_PATHS["models"] / "tts")


def _cleanup_partial_download(cache_dir):
    """Удаляет частично скачанную модель (битые zip/файлы)."""
    model_dir = os.path.join(cache_dir, MODEL_DIR_NAME)
    if os.path.exists(model_dir):
        # Проверяем, есть ли ключевые файлы модели (config.json, model.pth)
        has_config = os.path.exists(os.path.join(model_dir, "config.json"))
        has_model = any(
            f.endswith(".pth") for f in os.listdir(model_dir)
        ) if os.path.isdir(model_dir) else False
        if not (has_config and has_model):
            print(f"[download_tts] Удаление неполной загрузки: {model_dir}", flush=True)
            try:
                shutil.rmtree(model_dir)
            except Exception as e:
                print(f"[download_tts] Не удалось удалить {model_dir}: {e}", flush=True)

    # Удаляем .zip файлы — это промежуточные файлы загрузки
    for f in os.listdir(cache_dir) if os.path.isdir(cache_dir) else []:
        if f.endswith(".zip") and "xtts" in f.lower():
            zip_path = os.path.join(cache_dir, f)
            print(f"[download_tts] Удаление частичного архива: {zip_path}", flush=True)
            try:
                os.unlink(zip_path)
            except Exception:
                pass


def _is_model_ready(cache_dir):
    """Проверяет, что модель полностью скачана."""
    model_dir = os.path.join(cache_dir, MODEL_DIR_NAME)
    if not os.path.isdir(model_dir):
        return False
    has_config = os.path.exists(os.path.join(model_dir, "config.json"))
    has_model = any(f.endswith(".pth") for f in os.listdir(model_dir))
    return has_config and has_model


def main():
    cache_dir = _get_tts_cache_dir()
    os.makedirs(cache_dir, exist_ok=True)

    # Перенаправляем кэш Coqui TTS
    os.environ["COQUI_TTS_CACHE"] = cache_dir
    os.environ["COQUI_TOS_AGREED"] = "1"

    print(f"[download_tts] Кэш моделей TTS: {cache_dir}", flush=True)

    # Проверяем, уже ли модель скачана
    if _is_model_ready(cache_dir):
        print(f"[download_tts] Модель XTTS v2 уже загружена.", flush=True)
        return 0

    print(f"[download_tts] Скачивание модели XTTS v2 (~1.8 ГБ)...", flush=True)

    try:
        from TTS.api import TTS
    except ImportError:
        print("[download_tts] Coqui TTS не установлен, пропуск предзагрузки.", flush=True)
        return 0

    # Патчим директорию кэша
    try:
        import TTS.utils.manage as _tts_manage
        _orig = _tts_manage.get_user_data_dir
        def _patched(appname):
            if appname == "tts":
                os.makedirs(cache_dir, exist_ok=True)
                return cache_dir
            return _orig(appname)
        _tts_manage.get_user_data_dir = _patched
    except Exception:
        pass

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # Чистим битые файлы перед попыткой
            _cleanup_partial_download(cache_dir)

            print(f"[download_tts] Попытка {attempt}/{MAX_RETRIES}...", flush=True)
            tts = TTS(model_name=MODEL_NAME, progress_bar=True)
            del tts  # Не нужен, освобождаем память

            if _is_model_ready(cache_dir):
                print("[download_tts] Модель XTTS v2 успешно загружена.", flush=True)
                return 0
            else:
                raise RuntimeError("Модель загружена, но файлы не найдены — возможно, путь кэша некорректен")

        except Exception as e:
            last_error = e
            err_str = str(e).lower()
            is_network = any(kw in err_str for kw in [
                "download", "connection", "timeout", "urllib3",
                "requests", "ssl", "socket", "network", "failed to download",
            ]) or isinstance(e, (ConnectionError, OSError, TimeoutError))

            if is_network and attempt < MAX_RETRIES:
                wait = 10 * (2 ** (attempt - 1))  # 10, 20, 40, 80 сек
                print(
                    f"[download_tts] Ошибка сети: {e}\n"
                    f"[download_tts] Повтор через {wait} сек...",
                    flush=True
                )
                time.sleep(wait)
            else:
                print(f"[download_tts] Ошибка загрузки: {e}", flush=True)
                break

    print(
        f"[download_tts] Не удалось загрузить модель после {MAX_RETRIES} попыток.\n"
        f"[download_tts] Последняя ошибка: {last_error}\n"
        f"[download_tts] Модель скачается при первом запуске озвучки.",
        flush=True
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())

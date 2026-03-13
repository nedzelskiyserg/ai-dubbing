# -*- coding: utf-8 -*-
"""
Предзагрузка моделей F5-TTS в кэш huggingface.
Скачивает English и Russian модели с retry при сетевых ошибках.
Запуск: python download_tts_model.py
"""
import os
import sys
import time
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

# F5-TTS English model (SWivid/F5-TTS, auto-downloads ~3 GB)
EN_MODEL_REPO = "SWivid/F5-TTS"

# F5-TTS Russian model (Misha24-10/F5-TTS_RUSSIAN)
RU_MODEL_REPO = "Misha24-10/F5-TTS_RUSSIAN"
RU_VOCAB_FILE = "F5TTS_v1_Base/vocab.txt"
RU_CKPT_FILE = "F5TTS_v1_Base_v2/model_last_inference.safetensors"

MAX_RETRIES = 5


def _download_with_retry(download_fn, label):
    """Скачивает с retry при сетевых ошибках."""
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            print(f"[download_tts] {label}: attempt {attempt}/{MAX_RETRIES}...", flush=True)
            download_fn()
            print(f"[download_tts] {label}: OK", flush=True)
            return True
        except Exception as e:
            last_error = e
            err_str = str(e).lower()
            is_network = any(kw in err_str for kw in [
                "download", "connection", "timeout", "urllib3",
                "requests", "ssl", "socket", "network",
            ]) or isinstance(e, (ConnectionError, OSError, TimeoutError))

            if is_network and attempt < MAX_RETRIES:
                wait = 10 * (2 ** (attempt - 1))
                print(
                    f"[download_tts] Network error: {e}\n"
                    f"[download_tts] Retrying in {wait} sec...",
                    flush=True
                )
                time.sleep(wait)
            else:
                print(f"[download_tts] {label} error: {e}", flush=True)
                break

    print(f"[download_tts] {label} failed after {MAX_RETRIES} attempts: {last_error}", flush=True)
    return False


def main():
    print("[download_tts] Pre-downloading F5-TTS models...", flush=True)

    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        print("[download_tts] huggingface_hub not installed, skipping preload.", flush=True)
        return 0

    errors = 0

    # 1. English model — trigger F5-TTS internal download
    # F5-TTS auto-downloads from SWivid/F5-TTS when first loaded
    # We pre-download the checkpoint to cache
    print("[download_tts] Downloading F5-TTS English model (~3 GB)...", flush=True)
    ok = _download_with_retry(
        lambda: hf_hub_download(
            EN_MODEL_REPO,
            "F5TTS_v1_Base/model_1250000.safetensors",
        ),
        "F5-TTS English checkpoint"
    )
    if not ok:
        errors += 1

    # Also download English vocab
    _download_with_retry(
        lambda: hf_hub_download(EN_MODEL_REPO, "F5TTS_v1_Base/vocab.txt"),
        "F5-TTS English vocab"
    )

    # 2. Russian model (Misha24-10/F5-TTS_RUSSIAN)
    print("[download_tts] Downloading F5-TTS Russian model (~1 GB)...", flush=True)
    ok = _download_with_retry(
        lambda: hf_hub_download(RU_MODEL_REPO, RU_CKPT_FILE),
        "F5-TTS Russian checkpoint"
    )
    if not ok:
        errors += 1

    ok = _download_with_retry(
        lambda: hf_hub_download(RU_MODEL_REPO, RU_VOCAB_FILE),
        "F5-TTS Russian vocab"
    )
    if not ok:
        errors += 1

    if errors == 0:
        print("[download_tts] All F5-TTS models downloaded successfully.", flush=True)
    else:
        print(
            f"[download_tts] {errors} download(s) failed. "
            "Models will be downloaded on first TTS use.",
            flush=True
        )

    return 1 if errors > 0 else 0


if __name__ == "__main__":
    sys.exit(main())

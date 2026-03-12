# -*- coding: utf-8 -*-
"""
Subprocess worker для WhisperX транскрипции.

Запускается в отдельном процессе чтобы изолировать нативные краши
(segfault 0xC0000005 в PyAnnote VAD на Windows в Parallels/VM).
Если подпроцесс падает — API сервер выживает и может повторить
с другими настройками (silero VAD, CPU fallback).

Использование:
    python transcribe_subprocess.py <config.json> <result.json>
"""
import sys
import os
import json
import warnings

warnings.filterwarnings('ignore')


class NumpyEncoder(json.JSONEncoder):
    """JSON encoder, который умеет сериализовать numpy-типы."""
    def default(self, obj):
        try:
            import numpy as np
            if isinstance(obj, np.integer):
                return int(obj)
            if isinstance(obj, np.floating):
                return float(obj)
            if isinstance(obj, np.ndarray):
                return obj.tolist()
        except ImportError:
            pass
        try:
            return float(obj)
        except (TypeError, ValueError):
            return str(obj)


def main():
    config_path = sys.argv[1]
    result_path = sys.argv[2]

    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)

    # --- Setup environment ---
    models_dir = config.get('models_dir', '')
    if models_dir:
        os.environ.setdefault("HUGGINGFACE_HUB_CACHE", models_dir)
        os.environ.setdefault("HF_HOME", models_dir)

    sys.setrecursionlimit(4000)

    # --- Патч torch.load для PyTorch 2.6+ ---
    import torch
    import functools
    _orig_load = torch.load

    @functools.wraps(_orig_load)
    def _patched_load(*args, **kwargs):
        kwargs['weights_only'] = False
        return _orig_load(*args, **kwargs)

    torch.load = _patched_load

    # --- Патч torchaudio 2.10+ (AudioMetaData / info / backends removed) ---
    import torchaudio
    if not hasattr(torchaudio, 'AudioMetaData'):
        from dataclasses import dataclass

        @dataclass
        class _AudioMetaData:
            sample_rate: int = 0
            num_frames: int = 0
            num_channels: int = 1
            bits_per_sample: int = 16
            encoding: str = "PCM_S"

        torchaudio.AudioMetaData = _AudioMetaData

        if not hasattr(torchaudio, 'backend'):
            import types
            torchaudio.backend = types.ModuleType('torchaudio.backend')
            torchaudio.backend.common = types.ModuleType('torchaudio.backend.common')
            sys.modules['torchaudio.backend'] = torchaudio.backend
            sys.modules['torchaudio.backend.common'] = torchaudio.backend.common
        elif not hasattr(torchaudio.backend, 'common'):
            import types
            torchaudio.backend.common = types.ModuleType('torchaudio.backend.common')
            sys.modules['torchaudio.backend.common'] = torchaudio.backend.common
        torchaudio.backend.common.AudioMetaData = _AudioMetaData

    if not hasattr(torchaudio, 'info'):
        import soundfile as sf

        def _torchaudio_info(filepath, **kwargs):
            info = sf.info(str(filepath))
            return torchaudio.AudioMetaData(
                sample_rate=info.samplerate,
                num_frames=info.frames,
                num_channels=info.channels,
                bits_per_sample=16,
                encoding=info.subtype or "PCM_S",
            )

        torchaudio.info = _torchaudio_info

    if not hasattr(torchaudio, 'list_audio_backends'):
        torchaudio.list_audio_backends = lambda: ["soundfile"]
    if not hasattr(torchaudio, 'get_audio_backend'):
        torchaudio.get_audio_backend = lambda: "soundfile"
    if not hasattr(torchaudio, 'set_audio_backend'):
        torchaudio.set_audio_backend = lambda backend: None

    # --- Транскрипция ---
    try:
        import whisperx

        device = config['device']
        vad_method = config.get('vad_method', 'pyannote')

        print(f"[subprocess] Loading model: {config['model_size']} "
              f"device={device} vad={vad_method}", flush=True)

        model = whisperx.load_model(
            config['model_size'],
            device=device,
            compute_type=config['compute_type'],
            language=config.get('language'),
            download_root=config.get('download_root') or None,
            vad_method=vad_method,
        )

        batch_size = config.get('batch_size', 4)
        print(f"[subprocess] Transcribing: batch_size={batch_size}, "
              f"chunk_size=10", flush=True)

        result = model.transcribe(
            config['audio_path'],
            batch_size=batch_size,
            chunk_size=10
        )

        output = {
            "status": "ok",
            "segments": result.get("segments", []),
            "language": result.get("language", "en"),
        }

        n_seg = len(output['segments'])
        print(f"[subprocess] Done: {n_seg} segments, "
              f"lang={output['language']}", flush=True)

    except Exception as e:
        import traceback
        output = {
            "status": "error",
            "error": f"{type(e).__name__}: {e}",
            "traceback": traceback.format_exc(),
        }
        print(f"[subprocess] Error: {output['error']}", flush=True)

    # --- Сохраняем результат ---
    with open(result_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, cls=NumpyEncoder, ensure_ascii=False)


if __name__ == '__main__':
    main()

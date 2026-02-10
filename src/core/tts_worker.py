#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вспомогательный скрипт для генерации TTS через venv_tts.
Запускается в отдельном процессе с Python 3.11+ из venv_tts.

Оптимизации для качества:
- Прямой вызов синтезатора XTTS с тонкими настройками
- Температура и repetition_penalty для естественности
- Пост-обработка: нормализация громкости, удаление шумов
"""
import sys
import json
import os
import time
import tempfile
import warnings

# Подавляем предупреждения
warnings.filterwarnings("ignore")

# Устанавливаем переменную окружения для лицензии
os.environ["COQUI_TOS_AGREED"] = "1"

# Перенаправляем кэш Coqui TTS в папку приложения (до импорта TTS!)
# Среда может быть уже настроена через api_server, но на всякий случай ставим и здесь
try:
    from core.config import APP_PATHS as _APP_PATHS
    _tts_cache = str(_APP_PATHS["models"] / "tts")
    os.environ.setdefault("COQUI_TTS_CACHE", _tts_cache)
except Exception:
    pass  # worker может запускаться без core.config

# Максимальное количество попыток загрузки модели при сетевых ошибках
MAX_MODEL_LOAD_RETRIES = 3

# Глобальные переменные для кэширования
_cached_tts = None
_cached_model_name = None
_cached_synthesizer = None
_cached_speaker_embedding = None
_cached_speaker_wav_path = None
_cached_processed_ref = None

# --- Оптимальные параметры генерации XTTS v2 ---
# Эти параметры влияют на качество и естественность голоса
XTTS_CONFIG = {
    "temperature": 0.65,           # 0.0-1.0: ниже = более монотонно, выше = более вариативно (0.65 оптимально)
    "length_penalty": 1.0,         # штраф за длину (1.0 = нейтрально)
    "repetition_penalty": 2.0,     # штраф за повторения (2.0-5.0, выше = меньше повторов/заикания)
    "top_k": 50,                   # топ-K семплирование (50 оптимально)
    "top_p": 0.85,                 # nucleus sampling (0.8-0.95)
    "speed": 1.0,                  # скорость речи (1.0 = нормальная, до 1.15 для ускорения)
    "enable_text_splitting": True, # разбивать длинные тексты
}

# Профессиональные ограничения для качества дубляжа
MAX_TTS_SPEED = 1.15    # Максимальное ускорение TTS (больше ухудшает качество)
MIN_TTS_SPEED = 1.0     # Никогда не замедляем (качество падает)


def preprocess_reference_audio(speaker_wav: str, target_sr: int = 22050) -> str:
    """
    Предобработка референсного аудио для улучшения качества клонирования.

    - Конвертация в моно
    - Ресемплинг до 22050 Hz (оптимально для XTTS)
    - Нормализация громкости
    - Удаление тишины в начале/конце
    """
    try:
        from pydub import AudioSegment
        from pydub.effects import normalize, strip_silence

        audio = AudioSegment.from_file(speaker_wav)

        # Конвертируем в моно
        if audio.channels > 1:
            audio = audio.set_channels(1)

        # Ресемплинг до 22050 Hz
        if audio.frame_rate != target_sr:
            audio = audio.set_frame_rate(target_sr)

        # Нормализуем громкость
        audio = normalize(audio)

        # Удаляем тишину в начале и конце (порог -40dB, минимум 100ms тишины)
        audio = strip_silence(audio, silence_len=100, silence_thresh=-40)

        # Сохраняем во временный файл
        temp_path = speaker_wav.replace(".wav", "_processed.wav")
        audio.export(temp_path, format="wav")

        return temp_path
    except Exception as e:
        # Если не удалось обработать, возвращаем оригинал
        print(f"⚠️ Не удалось обработать референс: {e}", file=sys.stderr, flush=True)
        return speaker_wav


def postprocess_audio(audio_path: str) -> None:
    """
    Пост-обработка сгенерированного аудио.

    - Удаление шумов и артефактов (noisereduce)
    - Нормализация громкости
    - Удаление артефактов в начале/конце
    - Fade in/out для устранения щелчков
    """
    try:
        import numpy as np
        from scipy.io import wavfile
        from pydub import AudioSegment
        from pydub.effects import normalize

        # Шаг 1: Noise reduction (убирает "квакание" и металлический призвук)
        try:
            import noisereduce as nr

            # Читаем аудио как numpy array
            sample_rate, audio_data = wavfile.read(audio_path)

            # Конвертируем в float для обработки
            if audio_data.dtype == np.int16:
                audio_float = audio_data.astype(np.float32) / 32768.0
            elif audio_data.dtype == np.int32:
                audio_float = audio_data.astype(np.float32) / 2147483648.0
            else:
                audio_float = audio_data.astype(np.float32)

            # Применяем noise reduction
            # stationary=True - для стационарного шума (гул, шипение)
            # prop_decrease=0.75 - умеренное подавление (не убивает голос)
            reduced = nr.reduce_noise(
                y=audio_float,
                sr=sample_rate,
                stationary=True,
                prop_decrease=0.75,
                n_fft=512,
                hop_length=128
            )

            # Конвертируем обратно в int16
            reduced_int16 = (reduced * 32767).astype(np.int16)
            wavfile.write(audio_path, sample_rate, reduced_int16)

        except ImportError:
            print("⚠️ noisereduce не установлен, пропускаем шумоподавление", file=sys.stderr, flush=True)
        except Exception as nr_error:
            print(f"⚠️ Ошибка шумоподавления: {nr_error}", file=sys.stderr, flush=True)

        # Шаг 2: Нормализация и fade через pydub
        audio = AudioSegment.from_file(audio_path)

        # Нормализуем громкость до -3dB (оставляем headroom)
        audio = normalize(audio, headroom=3.0)

        # Добавляем короткий fade in/out для устранения щелчков
        fade_ms = 10
        if len(audio) > fade_ms * 2:
            audio = audio.fade_in(fade_ms).fade_out(fade_ms)

        # Сохраняем обратно
        audio.export(audio_path, format="wav")
    except Exception as e:
        print(f"⚠️ Пост-обработка не удалась: {e}", file=sys.stderr, flush=True)


def generate_tts_advanced(
    text: str,
    speaker_wav: str,
    output_path: str,
    language: str = "ru",
    model_name: str = "tts_models/multilingual/multi-dataset/xtts_v2",
    segment_info: dict = None,
    tts_speed: float = 1.0
) -> dict:
    """
    Генерирует аудио с помощью XTTS v2 с оптимальными настройками качества.

    Использует прямой доступ к синтезатору для тонкой настройки параметров.
    Кэширует speaker embedding для консистентности голоса между сегментами.

    Args:
        text: Текст для синтеза
        speaker_wav: Путь к референсному аудио спикера
        output_path: Путь для сохранения результата
        language: Язык синтеза
        model_name: Название модели TTS
        segment_info: Информация о сегменте для логирования
        tts_speed: Скорость речи (1.0 = нормально, до 1.15 для ускорения)
    """
    # Применяем ограничения скорости для качества
    effective_speed = max(MIN_TTS_SPEED, min(tts_speed, MAX_TTS_SPEED))
    global _cached_tts, _cached_model_name, _cached_synthesizer
    global _cached_speaker_embedding, _cached_speaker_wav_path, _cached_processed_ref

    load_start = time.time()

    try:
        from TTS.api import TTS

        # Патчим директорию кэша моделей на папку приложения
        try:
            import TTS.utils.manage as _tts_manage
            _orig_gudd = _tts_manage.get_user_data_dir
            def _patched_gudd(appname):
                custom = os.environ.get("COQUI_TTS_CACHE")
                if custom and appname == "tts":
                    os.makedirs(custom, exist_ok=True)
                    return custom
                return _orig_gudd(appname)
            _tts_manage.get_user_data_dir = _patched_gudd
        except Exception:
            pass

        # Нормализуем язык
        lang_map = {
            'RUSSIAN': 'ru', 'ENGLISH': 'en', 'SPANISH': 'es', 'FRENCH': 'fr',
            'GERMAN': 'de', 'ITALIAN': 'it', 'PORTUGUESE': 'pt', 'POLISH': 'pl',
            'TURKISH': 'tr', 'DUTCH': 'nl', 'CZECH': 'cs', 'ARABIC': 'ar',
            'CHINESE': 'zh-cn', 'HUNGARIAN': 'hu', 'KOREAN': 'ko', 'JAPANESE': 'ja', 'HINDI': 'hi'
        }
        normalized_language = lang_map.get(language.upper(), language.lower())

        # Кэшируем модель (с retry при сетевых ошибках)
        if _cached_tts is None or _cached_model_name != model_name:
            if segment_info:
                print(f"📦 [{segment_info['index']}/{segment_info['total']}] Загрузка модели XTTS...", file=sys.stderr, flush=True)
            else:
                print("📦 Загрузка модели XTTS...", file=sys.stderr, flush=True)

            last_err = None
            for _attempt in range(1, MAX_MODEL_LOAD_RETRIES + 1):
                try:
                    _cached_tts = TTS(model_name=model_name, progress_bar=False)
                    last_err = None
                    break
                except Exception as _dl_err:
                    last_err = _dl_err
                    err_str = str(_dl_err).lower()
                    is_network = (
                        isinstance(_dl_err, (ConnectionError, OSError, TimeoutError))
                        or any(kw in err_str for kw in [
                            "download", "connection", "timeout", "urllib3",
                            "requests", "ssl", "socket", "network", "failed to download",
                        ])
                    )
                    if is_network and _attempt < MAX_MODEL_LOAD_RETRIES:
                        wait = 5 * (2 ** (_attempt - 1))
                        print(
                            f"⚠️ Попытка {_attempt}/{MAX_MODEL_LOAD_RETRIES} загрузки модели не удалась: {_dl_err}\n"
                            f"   Повтор через {wait} сек...",
                            file=sys.stderr, flush=True
                        )
                        time.sleep(wait)
                    else:
                        raise

            if last_err is not None:
                raise last_err

            _cached_model_name = model_name

            # Получаем синтезатор для прямого доступа (если доступен)
            if hasattr(_cached_tts, 'synthesizer'):
                _cached_synthesizer = _cached_tts.synthesizer

            # Сбрасываем кэш speaker embedding при смене модели
            _cached_speaker_embedding = None
            _cached_speaker_wav_path = None
            _cached_processed_ref = None

            load_time = time.time() - load_start
            print(f"✅ Модель загружена за {load_time:.1f}с", file=sys.stderr, flush=True)
        else:
            load_time = 0.0

        # Генерируем аудио
        gen_start = time.time()

        if segment_info:
            speed_info = f", speed={effective_speed:.2f}x" if effective_speed != 1.0 else ""
            print(f"🎤 [{segment_info['index']}/{segment_info['total']}] Генерация ({len(text)} симв.{speed_info})...", file=sys.stderr, flush=True)

        # Кэшируем обработанный референс для одного и того же спикера
        if _cached_speaker_wav_path != speaker_wav or _cached_processed_ref is None:
            processed_ref = preprocess_reference_audio(speaker_wav)
            _cached_speaker_wav_path = speaker_wav
            _cached_processed_ref = processed_ref
            # Сбрасываем speaker embedding для нового референса
            _cached_speaker_embedding = None
            print(f"🎯 Референс обработан: {processed_ref}", file=sys.stderr, flush=True)
        else:
            processed_ref = _cached_processed_ref

        # Используем прямой вызов с оптимальными параметрами
        if _cached_synthesizer is not None and hasattr(_cached_synthesizer, 'tts'):
            # Прямой вызов синтезатора с расширенными параметрами
            try:
                import numpy as np
                from scipy.io import wavfile

                # Попытка использовать кэшированный speaker embedding для консистентности
                # (работает для моделей XTTS, которые поддерживают gpt_cond_latent и speaker_embedding)
                use_cached_embedding = False

                if (
                    _cached_speaker_embedding is not None and
                    hasattr(_cached_synthesizer, 'tts_model') and
                    hasattr(_cached_synthesizer.tts_model, 'inference')
                ):
                    use_cached_embedding = True

                if use_cached_embedding:
                    # Используем кэшированный embedding
                    gpt_cond_latent, speaker_embedding = _cached_speaker_embedding
                    wav = _cached_synthesizer.tts_model.inference(
                        text=text,
                        language=normalized_language,
                        gpt_cond_latent=gpt_cond_latent,
                        speaker_embedding=speaker_embedding,
                        temperature=XTTS_CONFIG["temperature"],
                        length_penalty=XTTS_CONFIG["length_penalty"],
                        repetition_penalty=XTTS_CONFIG["repetition_penalty"],
                        top_k=XTTS_CONFIG["top_k"],
                        top_p=XTTS_CONFIG["top_p"],
                        speed=effective_speed,
                        enable_text_splitting=XTTS_CONFIG["enable_text_splitting"],
                    )
                    # inference возвращает dict с 'wav'
                    if isinstance(wav, dict) and 'wav' in wav:
                        wav = wav['wav']
                else:
                    # Первый вызов для этого спикера - вычисляем и кэшируем embedding
                    wav = _cached_synthesizer.tts(
                        text=text,
                        speaker_wav=processed_ref,
                        language=normalized_language,
                        split_sentences=XTTS_CONFIG["enable_text_splitting"],
                        temperature=XTTS_CONFIG["temperature"],
                        length_penalty=XTTS_CONFIG["length_penalty"],
                        repetition_penalty=XTTS_CONFIG["repetition_penalty"],
                        top_k=XTTS_CONFIG["top_k"],
                        top_p=XTTS_CONFIG["top_p"],
                        speed=effective_speed,
                    )

                    # Пытаемся закэшировать speaker embedding для последующих вызовов
                    try:
                        if hasattr(_cached_synthesizer, 'tts_model'):
                            model = _cached_synthesizer.tts_model
                            if hasattr(model, 'get_conditioning_latents'):
                                gpt_cond_latent, speaker_embedding = model.get_conditioning_latents(
                                    audio_path=processed_ref,
                                    gpt_cond_len=30,  # Больше контекста = лучше качество
                                    max_ref_length=30
                                )
                                _cached_speaker_embedding = (gpt_cond_latent, speaker_embedding)
                                print(f"✅ Speaker embedding закэширован", file=sys.stderr, flush=True)
                    except Exception as emb_err:
                        # Не удалось закэшировать - не критично, будем вычислять каждый раз
                        print(f"⚠️ Не удалось закэшировать embedding: {emb_err}", file=sys.stderr, flush=True)

                # Определяем sample rate из конфига модели
                sample_rate = 24000  # XTTS v2 по умолчанию 24kHz
                if hasattr(_cached_synthesizer, 'output_sample_rate'):
                    sample_rate = _cached_synthesizer.output_sample_rate

                # Нормализуем и конвертируем в int16
                wav_np = np.array(wav)
                if len(wav_np) > 0:
                    max_val = np.max(np.abs(wav_np))
                    if max_val > 0:
                        wav_np = wav_np / max_val * 0.95  # Нормализация до 95%
                wav_int16 = (wav_np * 32767).astype(np.int16)

                wavfile.write(output_path, sample_rate, wav_int16)

            except Exception as synth_error:
                # Fallback к стандартному API
                print(f"⚠️ Прямой синтез не удался, использую стандартный API: {synth_error}", file=sys.stderr, flush=True)
                _cached_tts.tts_to_file(
                    text=text,
                    speaker_wav=processed_ref,
                    language=normalized_language,
                    file_path=output_path,
                    split_sentences=XTTS_CONFIG["enable_text_splitting"]
                )
        else:
            # Стандартный API (без расширенных параметров)
            _cached_tts.tts_to_file(
                text=text,
                speaker_wav=processed_ref,
                language=normalized_language,
                file_path=output_path,
                split_sentences=XTTS_CONFIG["enable_text_splitting"]
            )

        # Пост-обработка для улучшения качества
        postprocess_audio(output_path)

        # Не удаляем временный обработанный референс - он кэшируется для других сегментов
        # Файл будет автоматически удален при завершении процесса или смене спикера

        gen_time = time.time() - gen_start

        if segment_info:
            print(f"✅ [{segment_info['index']}/{segment_info['total']}] Готово за {gen_time:.1f}с", file=sys.stderr, flush=True)

        return {
            "success": True,
            "error": None,
            "load_time": load_time,
            "gen_time": gen_time
        }

    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"❌ Ошибка генерации: {e}\n{error_details}", file=sys.stderr, flush=True)
        return {
            "success": False,
            "error": str(e),
            "load_time": time.time() - load_start,
            "gen_time": 0.0
        }


# Обратная совместимость
def generate_tts(text: str, speaker_wav: str, output_path: str, language: str = "ru",
                 model_name: str = "tts_models/multilingual/multi-dataset/xtts_v2",
                 segment_info: dict = None, tts_speed: float = 1.0):
    """Обёртка для обратной совместимости — использует улучшенную версию."""
    return generate_tts_advanced(text, speaker_wav, output_path, language, model_name, segment_info, tts_speed)


if __name__ == "__main__":
    # Читаем аргументы из stdin (JSON)
    input_data = json.loads(sys.stdin.read())

    result = generate_tts(
        text=input_data["text"],
        speaker_wav=input_data["speaker_wav"],
        output_path=input_data["output_path"],
        language=input_data.get("language", "ru"),
        model_name=input_data.get("model_name", "tts_models/multilingual/multi-dataset/xtts_v2"),
        segment_info=input_data.get("segment_info"),
        tts_speed=input_data.get("tts_speed", 1.0)
    )

    # Выводим результат в stdout (JSON)
    print(json.dumps(result))

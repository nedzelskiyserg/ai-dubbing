"""
API сервер для связи React фронтенда с Python бэкендом
"""
import os
import sys
import traceback
from pathlib import Path

# Добавляем путь к src (для режима разработки; в PyInstaller bundle пути свои)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# --- Глобальный фикс SSL для embedded Python на Windows ---
# Embedded Python не имеет системных CA-сертификатов.
# Нужно настроить ДО любых сетевых вызовов (import requests, urllib3 и т.д.)
def _fix_ssl_globally():
    """Настраивает SSL-сертификаты для всех сетевых библиотек."""
    try:
        import certifi
        ca_bundle = certifi.where()
        # Для requests/urllib3 (используют эти env vars)
        os.environ.setdefault("SSL_CERT_FILE", ca_bundle)
        os.environ.setdefault("REQUESTS_CA_BUNDLE", ca_bundle)
        os.environ.setdefault("CURL_CA_BUNDLE", ca_bundle)
        # Для стандартной библиотеки ssl (urllib.request и т.д.)
        import ssl
        ssl_context = ssl.create_default_context(cafile=ca_bundle)
        ssl._create_default_https_context = lambda: ssl_context
    except ImportError:
        # certifi не установлен — на Windows отключаем проверку
        if sys.platform == 'win32':
            import ssl
            ssl._create_default_https_context = ssl._create_unverified_context
    except Exception:
        pass

_fix_ssl_globally()

try:
    from flask import Flask, request, jsonify, send_file
    from flask_cors import CORS
    import json
    import asyncio
    import threading
    import logging
    import signal
    from concurrent.futures import ThreadPoolExecutor
    import multiprocessing
    from werkzeug.serving import WSGIRequestHandler

    from core.downloader import download_video
    from core.transcriber import Transcriber
    from core.translator import Translator
    from core.smart_translator import SmartTranslator  # Умный перевод с учётом тайминга
    from core.corrector import SpeakerCorrector
    from core.voice_cloner import VoiceCloner
    from core.video_maker import VideoMaker
    from core.audio_separator import AudioSeparator
    from core.config import APP_PATHS
    # Кэш Hugging Face (Whisper, Pyannote, F5-TTS) — в папку приложения
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(APP_PATHS["models"]))
    os.environ.setdefault("HF_HOME", str(APP_PATHS["models"]))
except Exception:
    # В упакованном exe при падении на импорте пишем лог — пользователь не видит консоль
    if getattr(sys, "frozen", False):
        try:
            crash_dir = Path.home() / "Documents" / "AI Dubbing Studio"
            crash_dir.mkdir(parents=True, exist_ok=True)
            crash_log = crash_dir / "api-server-crash.log"
            with open(crash_log, "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
        except Exception:
            pass
    raise

app = Flask(__name__)
CORS(app)  # Разрешаем CORS для React

# Настраиваем логирование Flask - отключаем логирование для /api/logs
log = logging.getLogger('werkzeug')
log.setLevel(logging.WARNING)  # Только WARNING и выше

# Кастомный класс для фильтрации логов
class FilteredRequestHandler(WSGIRequestHandler):
    def log_request(self, code='-', size='-'):
        # Не логируем запросы к /api/logs
        if '/api/logs' not in self.path:
            super().log_request(code, size)

# Глобальные переменные для хранения состояния
processing_state = {
    'logs': [],
    'is_processing': False,
    'current_step': None,
    'progress': 0,  # Процент выполнения (0-100)
    'should_stop': False  # Флаг для остановки процесса
}

# Thread pool для выполнения длительных операций
executor = ThreadPoolExecutor(max_workers=1)

# Глобальная переменная для хранения текущего потока обработки
current_thread = None
process_lock = threading.Lock()

def add_log(message):
    """Добавляет сообщение в лог"""
    import sys
    import datetime
    
    # Форматируем сообщение с временем
    timestamp = datetime.datetime.now().strftime('%H:%M:%S')
    formatted_message = f"[{timestamp}] {message}"
    
    # Добавляем в состояние для API
    processing_state['logs'].append(formatted_message)
    # Ограничиваем количество логов (последние 1000)
    if len(processing_state['logs']) > 1000:
        processing_state['logs'] = processing_state['logs'][-1000:]
    
    # Выводим в консоль (на Windows cp1252 не кодирует эмодзи — избегаем UnicodeEncodeError)
    try:
        print(formatted_message, flush=True)
    except UnicodeEncodeError:
        safe = formatted_message.encode(sys.stdout.encoding or 'utf-8', errors='replace').decode(sys.stdout.encoding or 'utf-8')
        print(safe, flush=True)
    sys.stdout.flush()

@app.route('/api/health', methods=['GET'])
def health():
    """Проверка здоровья API"""
    return jsonify({'status': 'ok'})

@app.route('/api/logs', methods=['GET'])
def get_logs():
    """Получить все логи"""
    return jsonify({'logs': processing_state['logs']})

@app.route('/api/logs/clear', methods=['POST'])
def clear_logs():
    """Очистить логи"""
    processing_state['logs'] = []
    return jsonify({'status': 'cleared'})

@app.route('/api/process/youtube', methods=['POST'])
def process_youtube():
    """Обработка YouTube видео"""
    global current_thread
    
    data = request.json
    url = data.get('url')
    quality = data.get('quality', '1080p')
    
    if not url:
        return jsonify({'error': 'URL is required'}), 400
    
    # Останавливаем предыдущий процесс, если он есть
    with process_lock:
        if current_thread and current_thread.is_alive():
            processing_state['should_stop'] = True
            processing_state['is_processing'] = False
    
    # Запускаем обработку в отдельном потоке с возможностью прерывания
    thread = threading.Thread(
        target=process_youtube_sync, 
        args=(url, quality, data),
        daemon=True
    )
    thread.start()
    
    with process_lock:
        current_thread = thread
    
    return jsonify({'status': 'started'})

def process_youtube_sync(url, quality, options):
    """Синхронная обработка YouTube видео (выполняется в отдельном потоке)"""
    try:
        processing_state['is_processing'] = True
        processing_state['current_step'] = 'downloading'
        processing_state['progress'] = 0
        processing_state['should_stop'] = False
        
        # Проверяем флаг остановки перед началом
        if processing_state['should_stop']:
            processing_state['is_processing'] = False
            return
        
        add_log(f"📥 Скачивание видео: {url}")
        processing_state['progress'] = 5
        
        # Создаем callback для проверки остановки во время скачивания
        def check_stop_during_download():
            if processing_state['should_stop']:
                add_log("⏹️ Скачивание прервано пользователем")
                return True
            return False
        
        video_path = download_video(url, add_log, quality)
        
        # Проверяем флаг остановки после скачивания
        if processing_state['should_stop']:
            add_log("⏹️ Обработка остановлена пользователем")
            processing_state['is_processing'] = False
            processing_state['current_step'] = None
            processing_state['progress'] = 0
            return
        
        if not video_path:
            add_log("❌ Ошибка скачивания видео")
            processing_state['is_processing'] = False
            processing_state['progress'] = 0
            return
        
        # Проверяем флаг остановки после скачивания
        if processing_state['should_stop']:
            add_log("⏹️ Обработка остановлена пользователем")
            processing_state['is_processing'] = False
            processing_state['current_step'] = None
            processing_state['progress'] = 0
            return
        
        add_log(f"✅ Видео скачано: {os.path.basename(video_path)}")
        processing_state['progress'] = 15
        
        # Транскрипция
        if options.get('transcribe', True):
            processing_state['current_step'] = 'transcribing'
            processing_state['progress'] = 20
            add_log("🎤 Запуск транскрипции...")
            
            # Преобразуем model (LARGE -> large-v3) для конструктора Transcriber
            model_map = {
                'Tiny': 'tiny',
                'Base': 'base',
                'Small': 'small',
                'Medium': 'medium',
                'LARGE': 'large-v3'
            }
            model_size = model_map.get(options.get('model', 'LARGE'), options.get('model', 'large-v3').lower())
            
            # Проверяем флаг остановки перед началом транскрипции
            if processing_state['should_stop']:
                add_log("⏹️ Обработка остановлена пользователем")
                processing_state['is_processing'] = False
                processing_state['current_step'] = None
                processing_state['progress'] = 0
                return
            
            # Создаем callback для проверки остановки
            def check_should_stop():
                return processing_state.get('should_stop', False)
            
            # Создаем Transcriber с правильным model_size и callback для проверки остановки
            transcriber = Transcriber(
                model_size=model_size, 
                progress_callback=add_log,
                should_stop_callback=check_should_stop
            )
            
            # Преобразуем language (AUTO -> None, RU -> ru)
            language = options.get('language')
            if language and language.upper() == 'AUTO':
                language = None
            elif language:
                language = language.lower()
            
            enable_diarization = options.get('diarization', True)
            num_speakers = options.get('speakers')
            if num_speakers and isinstance(num_speakers, str) and num_speakers.upper() == 'AUTO':
                num_speakers = None
            elif num_speakers:
                num_speakers = int(num_speakers) if isinstance(num_speakers, (str, int)) else num_speakers
            
            result = transcriber.transcribe_full(
                video_path,
                language=language,
                num_speakers=num_speakers if enable_diarization else None
            )
            
            # Проверяем флаг остановки после транскрипции
            if processing_state['should_stop']:
                add_log("⏹️ Обработка остановлена пользователем")
                processing_state['is_processing'] = False
                processing_state['current_step'] = None
                processing_state['progress'] = 0
                return
            
            # Проверяем, была ли транскрипция прервана
            if isinstance(result, dict) and result.get("stopped"):
                add_log("⏹️ Обработка остановлена пользователем")
                processing_state['is_processing'] = False
                processing_state['current_step'] = None
                processing_state['progress'] = 0
                return
            
            # transcribe_full может возвращать словарь или список
            if isinstance(result, dict):
                segments = result.get("segments", [])
            else:
                segments = result if isinstance(result, list) else []
            
            add_log(f"✅ Транскрипция завершена: {len(segments)} сегментов")
            processing_state['progress'] = 50
            
            if processing_state['should_stop']:
                add_log("⏹️ Обработка остановлена пользователем")
                processing_state['is_processing'] = False
                processing_state['current_step'] = None
                processing_state['progress'] = 0
                return
            
            # Перевод
            if options.get('translate', False):
                processing_state['current_step'] = 'translating'
                processing_state['progress'] = 60

                # Проверяем флаг остановки перед переводом
                if processing_state['should_stop']:
                    add_log("⏹️ Обработка остановлена пользователем")
                    processing_state['is_processing'] = False
                    processing_state['current_step'] = None
                    processing_state['progress'] = 0
                    return

                # Создаем callback для проверки остановки
                def check_should_stop():
                    return processing_state.get('should_stop', False)

                target_lang = options.get('target_lang', 'ru')
                provider = options.get('provider', 'api')

                # Преобразуем target_lang (RUSSIAN -> ru)
                lang_map = {
                    'RUSSIAN': 'ru',
                    'ENGLISH': 'en',
                    'SPANISH': 'es',
                    'FRENCH': 'fr',
                    'GERMAN': 'de'
                }
                target_lang_code = lang_map.get(target_lang.upper(), target_lang.lower())

                # Если включено клонирование голоса — используем умный перевод с учётом тайминга
                use_smart_translation = options.get('voice_cloning', False)

                if use_smart_translation:
                    add_log("🎯 Запуск УМНОГО перевода (с учётом тайминга для дубляжа)...")
                    try:
                        # Определяем провайдер перевода: openrouter или ollama
                        translation_provider = provider  # 'api'→ollama, 'ollama'→ollama, 'openrouter'→openrouter
                        if translation_provider == 'api':
                            translation_provider = 'ollama'
                        translation_model = options.get('translation_model') or options.get('ollama_model', 'qwen2.5:7b')
                        openrouter_key = options.get('openrouter_api_key', '') or os.environ.get('OPENROUTER_API_KEY', '')

                        smart_translator = SmartTranslator(
                            model=translation_model,
                            provider=translation_provider,
                            api_key=openrouter_key,
                            progress_callback=add_log,
                            should_stop_callback=check_should_stop
                        )
                        # Нормализуем source_lang: 'AUTO' → None для автоопределения
                        src_lang = options.get('language')
                        if src_lang and src_lang.upper() == 'AUTO':
                            src_lang = None
                        elif src_lang:
                            src_lang = src_lang.lower()
                        translated_segments = smart_translator.translate_segments(
                            segments,
                            target_lang=target_lang_code,
                            source_lang=src_lang
                        )
                    except RuntimeError as e:
                        # Ollama не доступен — fallback на обычный перевод
                        add_log(f"⚠️ Умный перевод недоступен ({e}), используем обычный...")
                        use_smart_translation = False
                    except InterruptedError:
                        add_log("⏹️ Обработка остановлена пользователем")
                        processing_state['is_processing'] = False
                        processing_state['current_step'] = None
                        processing_state['progress'] = 0
                        return

                if not use_smart_translation:
                    add_log("🌍 Запуск перевода (API)...")
                    translator = Translator(
                        progress_callback=add_log,
                        should_stop_callback=check_should_stop
                    )

                    try:
                        translated_segments = translator.translate_segments(
                            segments,
                            target_lang=target_lang_code,
                            source_lang=options.get('language'),
                            model=options.get('model', 'large-v3'),
                            use_fallback=(provider == 'api'),
                            force_fallback=(provider == 'api')
                        )
                    except InterruptedError:
                        add_log("⏹️ Обработка остановлена пользователем")
                        processing_state['is_processing'] = False
                        processing_state['current_step'] = None
                        processing_state['progress'] = 0
                        return

                add_log(f"✅ Перевод завершен")
                segments = translated_segments
                processing_state['progress'] = 70
                
                # Сохраняем переведенную транскрипцию
                video_dir = os.path.dirname(video_path)
                video_name = os.path.splitext(os.path.basename(video_path))[0]
                translated_transcript_path = os.path.join(video_dir, f"{video_name}_translated_{target_lang_code}_transcript.txt")
                
                # Получаем список спикеров из переведенных сегментов
                translated_speakers_set = set(seg.get("speaker", "SPEAKER_UNKNOWN") for seg in translated_segments)
                
                # Формируем переведенный текстовый сценарий
                translated_transcript_text = f"СЦЕНАРИЙ (ПЕРЕВЕДЕННЫЙ НА {target_lang_code.upper()})\n"
                translated_transcript_text += "=" * 50 + "\n\n"
                
                current_speaker = None
                
                def format_timestamp(seconds):
                    m, s = divmod(seconds, 60)
                    h, m = divmod(m, 60)
                    return f"{int(h):02d}:{int(m):02d}:{int(s):02d}"
                
                for seg in translated_segments:
                    # Проверяем флаг остановки в цикле
                    if processing_state['should_stop']:
                        add_log("⏹️ Обработка остановлена пользователем")
                        processing_state['is_processing'] = False
                        processing_state['current_step'] = None
                        processing_state['progress'] = 0
                        return
                    
                    speaker = seg.get("speaker", "SPEAKER_UNKNOWN")
                    
                    if speaker != current_speaker:
                        translated_transcript_text += f"\n👤 {speaker} "
                        current_speaker = speaker
                    
                    start_time = format_timestamp(float(seg.get("start", 0)))
                    end_time = format_timestamp(float(seg.get("end", 0)))
                    text = seg.get("text", "").strip()
                    
                    translated_transcript_text += f"[{start_time} -> {end_time}]: {text}\n"
                
                translated_transcript_text += "\n" + "=" * 50 + "\n"
                translated_transcript_text += f"📊 СТАТИСТИКА:\n"
                translated_transcript_text += f"- Всего спикеров: {len(translated_speakers_set)}\n"
                translated_transcript_text += f"- Список спикеров: {', '.join(sorted(translated_speakers_set))}\n"
                translated_transcript_text += f"- Сегментов со спикерами: {len(translated_segments)}\n"
                
                with open(translated_transcript_path, 'w', encoding='utf-8') as f:
                    f.write(translated_transcript_text)
                add_log(f"💾 Переведенный сценарий сохранен: {translated_transcript_path}")
            
            if processing_state['should_stop']:
                add_log("⏹️ Обработка остановлена пользователем")
                processing_state['is_processing'] = False
                processing_state['current_step'] = None
                processing_state['progress'] = 0
                return
            
            # Voice Cloning
            if options.get('voice_cloning', False):
                processing_state['current_step'] = 'voice_cloning'
                processing_state['progress'] = 75
                add_log("🎤 Запуск клонирования голоса...")
                
                # Проверяем флаг остановки перед voice cloning
                if processing_state['should_stop']:
                    add_log("⏹️ Обработка остановлена пользователем")
                    processing_state['is_processing'] = False
                    processing_state['current_step'] = None
                    processing_state['progress'] = 0
                    return
                
                # Создаем callback для проверки остановки
                def check_should_stop():
                    return processing_state.get('should_stop', False)
                
                # Разделение вокала и инструментов (Demucs)
                instruments_path = None
                vocals_path = video_path
                try:
                    add_log("🎵 Разделение вокала от фоновой музыки (Demucs)...")
                    separator = AudioSeparator(progress_callback=add_log)
                    separated = separator.separate(video_path)
                    vocals_path = separated["vocals"]
                    instruments_path = separated.get("instruments")
                    separator.cleanup()
                except Exception as sep_err:
                    add_log(f"⚠️ Demucs недоступен: {sep_err}")
                    add_log("   Продолжаем с оригинальным аудио")
                    vocals_path = video_path
                    instruments_path = None

                if processing_state['should_stop']:
                    add_log("⏹️ Обработка остановлена пользователем")
                    processing_state['is_processing'] = False
                    processing_state['current_step'] = None
                    processing_state['progress'] = 0
                    return

                cloner = VoiceCloner(
                    progress_callback=add_log,
                    should_stop_callback=check_should_stop
                )
                use_preset = options.get('use_preset_voices', False)
                if use_preset:
                    speaker_samples = cloner.extract_speaker_samples_for_gender(
                        vocals_path,
                        segments
                    )
                else:
                    speaker_samples = cloner.extract_speaker_samples(
                        vocals_path,
                        segments
                    )

                # Проверяем флаг остановки после извлечения образцов
                if processing_state['should_stop']:
                    add_log("⏹️ Обработка остановлена пользователем")
                    processing_state['is_processing'] = False
                    processing_state['current_step'] = None
                    processing_state['progress'] = 0
                    return

                # Преобразуем target_lang для voice cloning (RUSSIAN -> ru)
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
                target_lang_for_voice = lang_map.get(options.get('target_lang', 'ru').upper(), options.get('target_lang', 'ru').lower())

                try:
                    use_preset = options.get('use_preset_voices', False)
                    segments_with_audio = cloner.generate_dubbing(
                        segments,
                        speaker_samples,
                        target_lang_for_voice,
                        use_preset_voices=use_preset
                    )
                except InterruptedError:
                    add_log("⏹️ Обработка остановлена пользователем")
                    processing_state['is_processing'] = False
                    processing_state['current_step'] = None
                    processing_state['progress'] = 0
                    return

                # Проверяем флаг остановки после генерации дубляжа
                if processing_state['should_stop']:
                    add_log("⏹️ Обработка остановлена пользователем")
                    processing_state['is_processing'] = False
                    processing_state['current_step'] = None
                    processing_state['progress'] = 0
                    return

                # Сохраняем файлы озвучки в папку Downloads
                import shutil
                downloads_dir = APP_PATHS['downloads']
                video_name = os.path.splitext(os.path.basename(video_path))[0]
                audio_output_dir = os.path.join(downloads_dir, f"{video_name}_audio")
                os.makedirs(audio_output_dir, exist_ok=True)
                
                saved_count = 0
                for i, seg in enumerate(segments_with_audio):
                    audio_file = seg.get("audio_file")
                    if audio_file and os.path.exists(audio_file):
                        # Копируем файл в папку Downloads
                        segment_filename = f"segment_{i:04d}_{seg.get('speaker', 'UNKNOWN')}.wav"
                        dest_path = os.path.join(audio_output_dir, segment_filename)
                        shutil.copy2(audio_file, dest_path)
                        saved_count += 1
                
                if saved_count > 0:
                    add_log(f"💾 Сохранено {saved_count} файлов озвучки в: {audio_output_dir}")
                
                # Создание финального видео
                processing_state['current_step'] = 'making_video'
                add_log("🎬 Создание финального видео...")
                
                # Проверяем флаг остановки перед созданием видео
                if processing_state['should_stop']:
                    add_log("⏹️ Обработка остановлена пользователем")
                    processing_state['is_processing'] = False
                    processing_state['current_step'] = None
                    processing_state['progress'] = 0
                    return
                
                # Создаем callback для проверки остановки
                def check_should_stop():
                    return processing_state.get('should_stop', False)
                
                video_maker = VideoMaker(
                    progress_callback=add_log,
                    should_stop_callback=check_should_stop
                )
                output_path = os.path.join(
                    os.path.dirname(video_path),
                    f"{os.path.splitext(os.path.basename(video_path))[0]}_dubbed.mp4"
                )
                
                try:
                    video_maker.make_video(
                        video_path,
                        segments_with_audio,
                        output_path,
                        instruments_path=instruments_path,
                        vocals_path=vocals_path,
                    )
                except InterruptedError:
                    add_log("⏹️ Обработка остановлена пользователем")
                    processing_state['is_processing'] = False
                    processing_state['current_step'] = None
                    processing_state['progress'] = 0
                    return

                # Проверяем флаг остановки после создания видео
                if processing_state['should_stop']:
                    add_log("⏹️ Обработка остановлена пользователем")
                    processing_state['is_processing'] = False
                    processing_state['current_step'] = None
                    processing_state['progress'] = 0
                    return
                
                add_log(f"✅ Видео создано: {output_path}")
        
        processing_state['is_processing'] = False
        processing_state['current_step'] = None
        add_log("✅ Обработка завершена!")
        
    except (InterruptedError, KeyboardInterrupt) as e:
        add_log("⏹️ Обработка прервана пользователем")
        processing_state['is_processing'] = False
        processing_state['current_step'] = None
        processing_state['progress'] = 0
        processing_state['should_stop'] = False
    except Exception as e:
        add_log(f"❌ Ошибка: {str(e)}")
        import traceback
        add_log(traceback.format_exc())
        processing_state['is_processing'] = False
        processing_state['current_step'] = None
    finally:
        # Всегда сбрасываем флаг остановки
        processing_state['should_stop'] = False

@app.route('/api/process/file', methods=['POST'])
def process_file():
    """Обработка загруженного файла"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    options = json.loads(request.form.get('options', '{}'))
    
    # Создаем папку проекта (как в downloader.py)
    downloads_dir = APP_PATHS['downloads']
    os.makedirs(downloads_dir, exist_ok=True)
    
    # Получаем имя файла без расширения для папки проекта
    file_name_without_ext = os.path.splitext(file.filename)[0]
    video_folder_name = f"{file_name_without_ext}_local"
    video_folder = os.path.join(downloads_dir, video_folder_name)
    os.makedirs(video_folder, exist_ok=True)
    
    add_log(f"📁 Создана папка проекта: {video_folder_name}")
    
    # Сохраняем файл в папку проекта
    file_path = os.path.join(video_folder, file.filename)
    file.save(file_path)
    
    add_log(f"📁 Файл загружен: {file.filename}")
    
    # Останавливаем предыдущий процесс, если он есть
    global current_thread
    with process_lock:
        if current_thread and current_thread.is_alive():
            processing_state['should_stop'] = True
            processing_state['is_processing'] = False
    
    # Запускаем обработку в отдельном потоке с возможностью прерывания
    thread = threading.Thread(
        target=process_file_sync, 
        args=(file_path, options),
        daemon=True
    )
    thread.start()
    
    with process_lock:
        current_thread = thread
    
    return jsonify({'status': 'started'})

def process_file_sync(file_path, options):
    """Синхронная обработка файла (выполняется в отдельном потоке)"""
    # Аналогично process_youtube_sync, но без скачивания
    try:
        processing_state['is_processing'] = True
        processing_state['progress'] = 0
        processing_state['should_stop'] = False
        
        # Транскрипция и остальные шаги
        if options.get('transcribe', True):
            processing_state['current_step'] = 'transcribing'
            processing_state['progress'] = 20
            add_log("🎤 Запуск транскрипции...")
            
            # Преобразуем model (LARGE -> large-v3) для конструктора Transcriber
            model_map = {
                'Tiny': 'tiny',
                'Base': 'base',
                'Small': 'small',
                'Medium': 'medium',
                'LARGE': 'large-v3'
            }
            model_size = model_map.get(options.get('model', 'LARGE'), options.get('model', 'large-v3').lower())
            
            # Проверяем флаг остановки перед началом транскрипции
            if processing_state['should_stop']:
                add_log("⏹️ Обработка остановлена пользователем")
                processing_state['is_processing'] = False
                processing_state['current_step'] = None
                processing_state['progress'] = 0
                return
            
            # Создаем callback для проверки остановки
            def check_should_stop():
                return processing_state.get('should_stop', False)
            
            # Создаем Transcriber с правильным model_size и callback для проверки остановки
            transcriber = Transcriber(
                model_size=model_size, 
                progress_callback=add_log,
                should_stop_callback=check_should_stop
            )
            
            # Преобразуем language (AUTO -> None, RU -> ru)
            language = options.get('language')
            if language and language.upper() == 'AUTO':
                language = None
            elif language:
                language = language.lower()
            
            enable_diarization = options.get('diarization', True)
            num_speakers = options.get('speakers')
            if num_speakers and isinstance(num_speakers, str) and num_speakers.upper() == 'AUTO':
                num_speakers = None
            elif num_speakers:
                num_speakers = int(num_speakers) if isinstance(num_speakers, (str, int)) else num_speakers
            
            result = transcriber.transcribe_full(
                file_path,
                language=language,
                num_speakers=num_speakers if enable_diarization else None
            )
            
            # Проверяем, была ли транскрипция прервана
            if isinstance(result, dict) and result.get("stopped"):
                add_log("⏹️ Обработка остановлена пользователем")
                processing_state['is_processing'] = False
                processing_state['current_step'] = None
                processing_state['progress'] = 0
                return
            
            # Проверяем флаг остановки после транскрипции
            if processing_state['should_stop']:
                add_log("⏹️ Обработка остановлена пользователем")
                processing_state['is_processing'] = False
                processing_state['current_step'] = None
                processing_state['progress'] = 0
                return
            
            # transcribe_full может возвращать словарь или список
            if isinstance(result, dict):
                segments = result.get("segments", [])
                detected_language = result.get("language", "en")
            else:
                segments = result if isinstance(result, list) else []
                detected_language = "en"
            
            add_log(f"✅ Транскрипция завершена: {len(segments)} сегментов")
            
            # Сохраняем скрипты транскрипции в папку проекта
            video_dir = os.path.dirname(file_path)
            video_name = os.path.splitext(os.path.basename(file_path))[0]
            
            # Пути для сохранения скриптов
            transcript_path = os.path.join(video_dir, f"{video_name}_transcript.txt")
            segments_path = os.path.join(video_dir, f"{video_name}_segments.json")
            
            # Сохраняем JSON сегментов
            import json as json_lib
            speakers_set = set(seg.get("speaker", "SPEAKER_UNKNOWN") for seg in segments)
            full_result_json = {
                "segments": segments,
                "language": detected_language,
                "language_probability": 0.99,
                "diarization": {
                    "total_speakers": len(speakers_set),
                    "speakers": sorted(list(speakers_set))
                }
            }
            with open(segments_path, 'w', encoding='utf-8') as f:
                json_lib.dump(full_result_json, f, ensure_ascii=False, indent=2)
            add_log(f"💾 Сегменты сохранены: {segments_path}")
            
            # Формируем и сохраняем текстовый сценарий
            transcript_text = "СЦЕНАРИЙ (WHISPERX PIPELINE)\n"
            transcript_text += "=" * 50 + "\n\n"
            
            current_speaker = None
            
            def format_timestamp(seconds):
                m, s = divmod(seconds, 60)
                h, m = divmod(m, 60)
                return f"{int(h):02d}:{int(m):02d}:{int(s):02d}"

            for seg in segments:
                # Проверяем флаг остановки в цикле
                if processing_state['should_stop']:
                    add_log("⏹️ Обработка остановлена пользователем")
                    processing_state['is_processing'] = False
                    processing_state['current_step'] = None
                    processing_state['progress'] = 0
                    return

                speaker = seg.get("speaker", "SPEAKER_UNKNOWN")

                if speaker != current_speaker:
                    transcript_text += f"\n👤 {speaker} "
                    current_speaker = speaker

                start_time = format_timestamp(float(seg.get("start", 0)))
                end_time = format_timestamp(float(seg.get("end", 0)))
                text = seg.get("text", "").strip()

                transcript_text += f"[{start_time} -> {end_time}]: {text}\n"
            
            transcript_text += "\n" + "=" * 50 + "\n"
            transcript_text += f"📊 СТАТИСТИКА:\n"
            transcript_text += f"- Всего спикеров: {len(speakers_set)}\n"
            transcript_text += f"- Список спикеров: {', '.join(sorted(speakers_set))}\n"
            transcript_text += f"- Сегментов со спикерами: {len(segments)}\n"
            
            with open(transcript_path, 'w', encoding='utf-8') as f:
                f.write(transcript_text)
            add_log(f"💾 Сценарий сохранен: {transcript_path}")
            processing_state['progress'] = 50
            
            if processing_state['should_stop']:
                add_log("⏹️ Обработка остановлена пользователем")
                processing_state['is_processing'] = False
                processing_state['current_step'] = None
                processing_state['progress'] = 0
                return
            
            # Перевод
            if options.get('translate', False):
                processing_state['current_step'] = 'translating'
                processing_state['progress'] = 60

                # Проверяем флаг остановки перед переводом
                if processing_state['should_stop']:
                    add_log("⏹️ Обработка остановлена пользователем")
                    processing_state['is_processing'] = False
                    processing_state['current_step'] = None
                    processing_state['progress'] = 0
                    return

                # Создаем callback для проверки остановки
                def check_should_stop():
                    return processing_state.get('should_stop', False)

                # Преобразуем target_lang (RUSSIAN -> ru)
                lang_map = {
                    'RUSSIAN': 'ru',
                    'ENGLISH': 'en',
                    'SPANISH': 'es',
                    'FRENCH': 'fr',
                    'GERMAN': 'de'
                }
                target_lang_code = lang_map.get(options.get('target_lang', 'ru').upper(), options.get('target_lang', 'ru').lower())
                provider = options.get('provider', 'api')

                # Преобразуем language для source_lang
                source_lang = options.get('language')
                if source_lang and source_lang.upper() == 'AUTO':
                    source_lang = None
                elif source_lang:
                    source_lang = source_lang.lower()

                # Если включено клонирование голоса — используем умный перевод с учётом тайминга
                use_smart_translation = options.get('voice_cloning', False)

                if use_smart_translation:
                    add_log("🎯 Запуск УМНОГО перевода (с учётом тайминга для дубляжа)...")
                    try:
                        translation_provider = provider
                        if translation_provider == 'api':
                            translation_provider = 'ollama'
                        translation_model = options.get('translation_model') or options.get('ollama_model', 'qwen2.5:7b')
                        openrouter_key = options.get('openrouter_api_key', '') or os.environ.get('OPENROUTER_API_KEY', '')

                        smart_translator = SmartTranslator(
                            model=translation_model,
                            provider=translation_provider,
                            api_key=openrouter_key,
                            progress_callback=add_log,
                            should_stop_callback=check_should_stop
                        )
                        segments = smart_translator.translate_segments(
                            segments,
                            target_lang=target_lang_code,
                            source_lang=source_lang
                        )
                    except RuntimeError as e:
                        # Ollama не доступен — fallback на обычный перевод
                        add_log(f"⚠️ Умный перевод недоступен ({e}), используем обычный...")
                        use_smart_translation = False
                    except InterruptedError:
                        add_log("⏹️ Обработка остановлена пользователем")
                        processing_state['is_processing'] = False
                        processing_state['current_step'] = None
                        processing_state['progress'] = 0
                        return

                if not use_smart_translation:
                    add_log("🌍 Запуск перевода (API)...")
                    translator = Translator(
                        progress_callback=add_log,
                        should_stop_callback=check_should_stop
                    )

                    # Преобразуем model (LARGE -> large-v3)
                    model_map = {
                        'Tiny': 'tiny',
                        'Base': 'base',
                        'Small': 'small',
                        'Medium': 'medium',
                        'LARGE': 'large-v3'
                    }
                    model_size = model_map.get(options.get('model', 'LARGE'), options.get('model', 'large-v3').lower())

                    try:
                        segments = translator.translate_segments(
                            segments,
                            target_lang=target_lang_code,
                            source_lang=source_lang,
                            model=model_size,
                            use_fallback=(provider == 'api'),
                            force_fallback=(provider == 'api')
                        )
                    except InterruptedError:
                        add_log("⏹️ Обработка остановлена пользователем")
                        processing_state['is_processing'] = False
                        processing_state['current_step'] = None
                        processing_state['progress'] = 0
                        return

                add_log(f"✅ Перевод завершен")
                processing_state['progress'] = 70
                
                # Сохраняем переведенную транскрипцию
                video_dir = os.path.dirname(file_path)
                video_name = os.path.splitext(os.path.basename(file_path))[0]
                translated_transcript_path = os.path.join(video_dir, f"{video_name}_translated_{target_lang_code}_transcript.txt")
                
                # Получаем список спикеров из переведенных сегментов
                translated_speakers_set = set(seg.get("speaker", "SPEAKER_UNKNOWN") for seg in segments)
                
                # Формируем переведенный текстовый сценарий
                translated_transcript_text = f"СЦЕНАРИЙ (ПЕРЕВЕДЕННЫЙ НА {target_lang_code.upper()})\n"
                translated_transcript_text += "=" * 50 + "\n\n"
                
                current_speaker = None
                
                def format_timestamp(seconds):
                    m, s = divmod(seconds, 60)
                    h, m = divmod(m, 60)
                    return f"{int(h):02d}:{int(m):02d}:{int(s):02d}"
                
                for seg in segments:
                    # Проверяем флаг остановки в цикле
                    if processing_state['should_stop']:
                        add_log("⏹️ Обработка остановлена пользователем")
                        processing_state['is_processing'] = False
                        processing_state['current_step'] = None
                        processing_state['progress'] = 0
                        return
                    
                    speaker = seg.get("speaker", "SPEAKER_UNKNOWN")
                    
                    if speaker != current_speaker:
                        translated_transcript_text += f"\n👤 {speaker} "
                        current_speaker = speaker
                    
                    start_time = format_timestamp(float(seg.get("start", 0)))
                    end_time = format_timestamp(float(seg.get("end", 0)))
                    text = seg.get("text", "").strip()
                    
                    translated_transcript_text += f"[{start_time} -> {end_time}]: {text}\n"
                
                translated_transcript_text += "\n" + "=" * 50 + "\n"
                translated_transcript_text += f"📊 СТАТИСТИКА:\n"
                translated_transcript_text += f"- Всего спикеров: {len(translated_speakers_set)}\n"
                translated_transcript_text += f"- Список спикеров: {', '.join(sorted(translated_speakers_set))}\n"
                translated_transcript_text += f"- Сегментов со спикерами: {len(segments)}\n"
                
                with open(translated_transcript_path, 'w', encoding='utf-8') as f:
                    f.write(translated_transcript_text)
                add_log(f"💾 Переведенный сценарий сохранен: {translated_transcript_path}")
            
            if processing_state['should_stop']:
                add_log("⏹️ Обработка остановлена пользователем")
                processing_state['is_processing'] = False
                processing_state['current_step'] = None
                processing_state['progress'] = 0
                return
            
            # Voice Cloning
            if options.get('voice_cloning', False):
                processing_state['current_step'] = 'voice_cloning'
                processing_state['progress'] = 75
                add_log("🎤 Запуск клонирования голоса...")
                
                # Проверяем флаг остановки перед voice cloning
                if processing_state['should_stop']:
                    add_log("⏹️ Обработка остановлена пользователем")
                    processing_state['is_processing'] = False
                    processing_state['current_step'] = None
                    processing_state['progress'] = 0
                    return
                
                # Создаем callback для проверки остановки
                def check_should_stop():
                    return processing_state.get('should_stop', False)
                
                # Разделение вокала и инструментов (Demucs)
                instruments_path = None
                vocals_path = file_path
                try:
                    add_log("🎵 Разделение вокала от фоновой музыки (Demucs)...")
                    separator = AudioSeparator(progress_callback=add_log)
                    separated = separator.separate(file_path)
                    vocals_path = separated["vocals"]
                    instruments_path = separated.get("instruments")
                    separator.cleanup()
                except Exception as sep_err:
                    add_log(f"⚠️ Demucs недоступен: {sep_err}")
                    add_log("   Продолжаем с оригинальным аудио")
                    vocals_path = file_path
                    instruments_path = None

                if processing_state['should_stop']:
                    add_log("⏹️ Обработка остановлена пользователем")
                    processing_state['is_processing'] = False
                    processing_state['current_step'] = None
                    processing_state['progress'] = 0
                    return

                cloner = VoiceCloner(
                    progress_callback=add_log,
                    should_stop_callback=check_should_stop
                )
                use_preset = options.get('use_preset_voices', False)
                if use_preset:
                    speaker_samples = cloner.extract_speaker_samples_for_gender(
                        vocals_path,
                        segments
                    )
                else:
                    speaker_samples = cloner.extract_speaker_samples(
                        vocals_path,
                        segments
                    )
                
                # Проверяем флаг остановки после извлечения образцов
                if processing_state['should_stop']:
                    add_log("⏹️ Обработка остановлена пользователем")
                    processing_state['is_processing'] = False
                    processing_state['current_step'] = None
                    processing_state['progress'] = 0
                    return
                
                # Преобразуем target_lang для voice cloning (RUSSIAN -> ru)
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
                target_lang_for_voice = lang_map.get(options.get('target_lang', 'ru').upper(), options.get('target_lang', 'ru').lower())
                
                try:
                    use_preset = options.get('use_preset_voices', False)
                    segments_with_audio = cloner.generate_dubbing(
                        segments,
                        speaker_samples,
                        target_lang_for_voice,
                        use_preset_voices=use_preset
                    )
                except InterruptedError:
                    add_log("⏹️ Обработка остановлена пользователем")
                    processing_state['is_processing'] = False
                    processing_state['current_step'] = None
                    processing_state['progress'] = 0
                    return
                
                # Проверяем флаг остановки после генерации дубляжа
                if processing_state['should_stop']:
                    add_log("⏹️ Обработка остановлена пользователем")
                    processing_state['is_processing'] = False
                    processing_state['current_step'] = None
                    processing_state['progress'] = 0
                    return
                
                # Сохраняем файлы озвучки в папку Downloads
                import shutil
                downloads_dir = APP_PATHS['downloads']
                video_name = os.path.splitext(os.path.basename(file_path))[0]
                audio_output_dir = os.path.join(downloads_dir, f"{video_name}_audio")
                os.makedirs(audio_output_dir, exist_ok=True)
                
                saved_count = 0
                for i, seg in enumerate(segments_with_audio):
                    # Проверяем флаг остановки в цикле
                    if processing_state['should_stop']:
                        add_log("⏹️ Обработка остановлена пользователем")
                        processing_state['is_processing'] = False
                        processing_state['current_step'] = None
                        processing_state['progress'] = 0
                        return
                    
                    audio_file = seg.get("audio_file")
                    if audio_file and os.path.exists(audio_file):
                        # Копируем файл в папку Downloads
                        segment_filename = f"segment_{i:04d}_{seg.get('speaker', 'UNKNOWN')}.wav"
                        dest_path = os.path.join(audio_output_dir, segment_filename)
                        shutil.copy2(audio_file, dest_path)
                        saved_count += 1
                
                if saved_count > 0:
                    add_log(f"💾 Сохранено {saved_count} файлов озвучки в: {audio_output_dir}")
                
                processing_state['progress'] = 85
                
                if processing_state['should_stop']:
                    add_log("⏹️ Обработка остановлена пользователем")
                    processing_state['is_processing'] = False
                    processing_state['current_step'] = None
                    processing_state['progress'] = 0
                    return
                
                # Создание финального видео
                processing_state['current_step'] = 'making_video'
                processing_state['progress'] = 90
                add_log("🎬 Создание финального видео...")
                
                # Проверяем флаг остановки перед созданием видео
                if processing_state['should_stop']:
                    add_log("⏹️ Обработка остановлена пользователем")
                    processing_state['is_processing'] = False
                    processing_state['current_step'] = None
                    processing_state['progress'] = 0
                    return
                
                # Создаем callback для проверки остановки
                def check_should_stop():
                    return processing_state.get('should_stop', False)
                
                video_maker = VideoMaker(
                    progress_callback=add_log,
                    should_stop_callback=check_should_stop
                )
                output_path = os.path.join(
                    os.path.dirname(file_path),
                    f"{os.path.splitext(os.path.basename(file_path))[0]}_dubbed.mp4"
                )
                
                try:
                    video_maker.make_video(
                        file_path,
                        segments_with_audio,
                        output_path,
                        instruments_path=instruments_path,
                        vocals_path=vocals_path,
                    )
                except InterruptedError:
                    add_log("⏹️ Обработка остановлена пользователем")
                    processing_state['is_processing'] = False
                    processing_state['current_step'] = None
                    processing_state['progress'] = 0
                    return
                
                # Проверяем флаг остановки после создания видео
                if processing_state['should_stop']:
                    add_log("⏹️ Обработка остановлена пользователем")
                    processing_state['is_processing'] = False
                    processing_state['current_step'] = None
                    processing_state['progress'] = 0
                    return
                
                add_log(f"✅ Видео создано: {output_path}")
        
        processing_state['progress'] = 100
        processing_state['is_processing'] = False
        processing_state['current_step'] = None
        add_log("✅ Обработка завершена!")
        
    except (InterruptedError, KeyboardInterrupt) as e:
        add_log("⏹️ Обработка прервана пользователем")
        processing_state['is_processing'] = False
        processing_state['current_step'] = None
        processing_state['progress'] = 0
        processing_state['should_stop'] = False
    except Exception as e:
        add_log(f"❌ Ошибка: {str(e)}")
        import traceback
        add_log(traceback.format_exc())
        processing_state['is_processing'] = False
        processing_state['current_step'] = None
        processing_state['progress'] = 0
    finally:
        # Всегда сбрасываем флаг остановки
        processing_state['should_stop'] = False

@app.route('/api/status', methods=['GET'])
def get_status():
    """Получить статус обработки"""
    return jsonify({
        'is_processing': processing_state['is_processing'],
        'current_step': processing_state['current_step'],
        'progress': processing_state['progress']
    })

@app.route('/api/stop', methods=['POST'])
def stop_processing():
    """Остановить обработку"""
    global current_thread
    
    add_log("⏹️ Запрос на остановку обработки...")
    force_stop_all_processing()
    
    return jsonify({'status': 'stopping'})

@app.route('/api/open-folder', methods=['POST'])
def open_folder():
    """Открыть папку Downloads с сохраненными обработками"""
    import subprocess
    import platform
    
    # Открываем папку Downloads, где сохраняются все обработки
    folder_path = str(APP_PATHS['downloads'])
    
    # Создаем папку если её нет
    os.makedirs(folder_path, exist_ok=True)
    
    try:
        if platform.system() == 'Darwin':  # macOS
            subprocess.run(['open', folder_path])
        elif platform.system() == 'Windows':
            subprocess.run(['explorer', folder_path])
        else:  # Linux
            subprocess.run(['xdg-open', folder_path])
        
        add_log(f"📂 Открыта папка: {folder_path}")
        return jsonify({'status': 'opened', 'path': folder_path})
    except Exception as e:
        add_log(f"❌ Ошибка открытия папки: {str(e)}")
        return jsonify({'error': str(e)}), 500

def force_stop_all_processing():
    """Принудительно останавливает все процессы обработки"""
    global current_thread
    
    add_log("⏹️ Принудительная остановка всех процессов...")
    processing_state['should_stop'] = True
    processing_state['is_processing'] = False
    processing_state['current_step'] = None
    processing_state['progress'] = 0
    
    # Принудительно останавливаем поток, если он еще работает
    with process_lock:
        if current_thread and current_thread.is_alive():
            add_log("⏹️ Принудительное завершение потока обработки...")
            # Поток будет проверять флаг и завершится сам
            # Даем немного времени на корректное завершение
            import time
            time.sleep(0.5)
    
    add_log("⏹️ Все процессы остановлены")

def signal_handler(signum, frame):
    """Обработчик сигналов для принудительного завершения"""
    try:
        signal_name = signal.Signals(signum).name
    except (ValueError, AttributeError):
        signal_name = f"Signal {signum}"
    
    try:
        add_log(f"⏹️ Получен сигнал {signal_name}, принудительно останавливаем все процессы...")
        force_stop_all_processing()
    except Exception as e:
        print(f"Ошибка при остановке процессов: {e}")
    
    # Даем время на завершение, затем выходим
    import time
    time.sleep(1)
    sys.exit(0)

if __name__ == '__main__':
    # На Windows в exe stdout по умолчанию cp1252 — эмодзи в логах дают UnicodeEncodeError. Включаем UTF-8.
    if getattr(sys, 'frozen', False) and sys.platform == 'win32':
        try:
            sys.stdout.reconfigure(encoding='utf-8')
            sys.stderr.reconfigure(encoding='utf-8')
        except Exception:
            pass
    # Регистрируем обработчики сигналов для принудительного завершения
    # SIGTERM доступен на Unix-системах (Linux, macOS)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, signal_handler)
    # SIGINT доступен на всех платформах (Ctrl+C)
    signal.signal(signal.SIGINT, signal_handler)
    
    # На Windows также обрабатываем SIGBREAK
    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, signal_handler)
    
    # Создаем директории если их нет
    os.makedirs(APP_PATHS['downloads'], exist_ok=True)
    
    # Настраиваем логирование - отключаем шумные логи Flask
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    
    # Порт: из переменной или ищем свободный в 5001–5010 (если 5001 занят — берём следующий)
    import socket
    def find_free_port(start=5001, end=5010):
        for p in range(start, end + 1):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(('', p))
                    return p
            except OSError:
                continue
        return start  # fallback — тогда app.run выдаст "Address already in use"
    port = int(os.getenv('API_PORT', 0)) or find_free_port()
    port_file = os.getenv('API_PORT_FILE')
    # При запуске из Electron (API_PORT_FILE задан) отключаем reloader: иначе Flask создаёт
    # второй процесс с другим портом, и Electron проверяет не тот порт. Debug в этом режиме тоже выключаем.
    use_debug = not bool(port_file)
    use_reloader = not bool(port_file)
    if port_file:
        try:
            # Path() нормализует слэши под ОС (важно для Windows при запуске из .exe)
            port_path = Path(port_file).resolve()
            port_path.parent.mkdir(parents=True, exist_ok=True)
            port_path.write_text(str(port), encoding='utf-8')
        except Exception:
            pass
    if port != 5001:
        print(f"Порт 5001 занят, используем порт {port}", flush=True)
    app.run(host='0.0.0.0', port=port, debug=use_debug, use_reloader=use_reloader, request_handler=FilteredRequestHandler)

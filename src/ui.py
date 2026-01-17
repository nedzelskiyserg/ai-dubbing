from nicegui import ui, run
import core.downloader as downloader
from core.transcriber import Transcriber
from core.diarization import Diarizer, merge_transcription_with_diarization
from core.config import APP_PATHS, open_folder 
import asyncio
import os
import json

def build_interface():
    # --- 1. CSS ФИКСЫ (ОСТАВЛЯЕМ ДЛЯ СТАБИЛЬНОСТИ) ---
    # Это нужно, чтобы терминал прилипал к краям и не было белых рамок
    ui.add_head_html('''
        <style>
            body { margin: 0; padding: 0; overflow: hidden; }
            .nicegui-content { padding: 0 !important; margin: 0 !important; height: 100vh; width: 100vw; }
            .q-splitter__panel { padding: 0 !important; overflow: hidden !important; position: relative !important; }
            
            /* Скроллбар для терминала */
            ::-webkit-scrollbar { width: 10px; height: 10px; }
            ::-webkit-scrollbar-track { background: #1e1e1e; }
            ::-webkit-scrollbar-thumb { background: #424242; }
            
            /* Возможность выделения текста в логе */
            .q-log,
            .q-log *,
            [class*="log"],
            [id*="log"] {
                user-select: text !important;
                -webkit-user-select: text !important;
                -moz-user-select: text !important;
                -ms-user-select: text !important;
            }
            
            /* Применяем к любому элементу с классом содержащим log */
            div[class*="log"],
            pre[class*="log"],
            code[class*="log"] {
                user-select: text !important;
                -webkit-user-select: text !important;
                -moz-user-select: text !important;
                -ms-user-select: text !important;
            }
        </style>
    ''')

    # Твои цвета
    ui.colors(primary='#5898d4', secondary='#26a69a', accent='#ea5455', dark='#1d1d1d')

    # --- ХЕДЕР (ТВОЙ) ---
    with ui.header().classes('items-center justify-between text-white h-14'):
        ui.label('AI Dubbing Studio').classes('text-xl font-bold q-ml-md')
        
        ui.button(icon='folder', on_click=lambda: open_folder(APP_PATHS['downloads'])) \
            .props('flat round dense') \
            .tooltip('Открыть папку') \
            .classes('q-mr-md text-white')

    # --- СОСТОЯНИЕ ---
    link_input = None
    quality_select = None
    log_view = None
    downloaded_video_path = None
    transcribe_checkbox = None
    diarize_checkbox = None
    hf_token_input = None
    model_size_select = None
    language_select = None

    # --- ЛОГИКА ---
    def smart_log(message):
        nonlocal log_view
        if log_view:
            log_view.push(message)
            ui.run_javascript(f'var el = getElement({log_view.id}); if(el) el.scrollTop = el.scrollHeight;')

    def clear_log():
        nonlocal log_view
        if log_view:
            log_view.clear()

    async def start_processing():
        url = link_input.value
        quality = quality_select.value 
        if not url:
            ui.notify('Ошибка: Введите ссылку!', color='negative')
            return
        
        smart_log(f"\n🚀 ЗАПУСК: {url} [{quality}]")
        smart_log("─" * 40)
        
        result_path = await run.io_bound(downloader.download_video, url, smart_log, quality)
        
        if result_path:
            nonlocal downloaded_video_path
            downloaded_video_path = result_path
            ui.notify('Готово!', type='positive')
            smart_log(f"✅ СОХРАНЕНО: {result_path}")
            
            # Если чекбокс транскрипции включен, запускаем автоматически
            if transcribe_checkbox and transcribe_checkbox.value:
                smart_log(f"📝 Автоматический запуск транскрипции...")
                await start_transcription()
            else:
                smart_log(f"📝 Видео готово. Включите 'Транскрибировать после скачивания' для автоматической транскрипции.")
    
    async def start_transcription():
        """Запускает транскрипцию и диаризацию видео в отдельном потоке"""
        nonlocal downloaded_video_path
        
        if not downloaded_video_path or not os.path.exists(downloaded_video_path):
            ui.notify('Ошибка: Сначала скачайте видео!', color='negative')
            return
        
        # Отключаем чекбоксы во время обработки
        if transcribe_checkbox:
            transcribe_checkbox.set_enabled(False)
        if diarize_checkbox:
            diarize_checkbox.set_enabled(False)
        
        model_size = model_size_select.value if model_size_select else 'base'
        language = language_select.value if language_select else None
        enable_diarization = diarize_checkbox.value if diarize_checkbox else False
        
        # Получаем токен: сначала из переменных окружения, потом из поля ввода
        hf_token = os.getenv('HF_TOKEN', '').strip()
        if not hf_token and hf_token_input and hf_token_input.value:
            hf_token = hf_token_input.value.strip()
        
        if enable_diarization and not hf_token:
            smart_log("⚠️ ВНИМАНИЕ: Hugging Face токен не найден!")
            smart_log("💡 Установите токен в .env файл или введите в поле выше")
        
        smart_log(f"\n🎤 ЗАПУСК ТРАНСКРИПЦИИ")
        smart_log("─" * 40)
        smart_log(f"📁 Файл: {os.path.basename(downloaded_video_path)}")
        smart_log(f"🤖 Модель: {model_size}")
        smart_log(f"🌍 Язык: {language if language else 'Автоопределение'}")
        smart_log(f"👥 Диаризация: {'Включена' if enable_diarization else 'Выключена'}")
        
        try:
            # Создаем транскрибер с callback для прогресса
            transcriber = Transcriber(
                model_size=model_size,
                device="auto",
                progress_callback=smart_log
            )
            
            # Запускаем транскрипцию в executor (неблокирующий режим)
            result = await run.io_bound(
                transcriber.transcribe,
                downloaded_video_path,
                language=language,
                word_timestamps=True,
                vad_filter=True
            )
            
            # Если включена диаризация, выполняем её
            if enable_diarization:
                smart_log(f"\n👥 ЗАПУСК ДИАРИЗАЦИИ")
                smart_log("─" * 40)
                
                try:
                    diarizer = Diarizer(
                        hf_token=hf_token,
                        progress_callback=smart_log
                    )
                    
                    # Запускаем диаризацию в executor
                    diarization_result = await run.io_bound(
                        diarizer.diarize,
                        downloaded_video_path
                    )
                    
                    # Связываем транскрипцию с диаризацией
                    smart_log("🔗 Связывание транскрипции с диаризацией...")
                    merged_segments = merge_transcription_with_diarization(
                        result['segments'],
                        diarization_result['segments']
                    )
                    
                    # Обновляем результат с информацией о спикерах
                    result['segments'] = merged_segments
                    result['diarization'] = {
                        'speakers': diarization_result['speakers'],
                        'total_speakers': len(diarization_result['speakers']),
                        'diarization_segments': diarization_result['segments']
                    }
                    
                    smart_log(f"✅ Диаризация завершена!")
                    smart_log(f"👥 Найдено спикеров: {len(diarization_result['speakers'])}")
                    
                except Exception as e:
                    smart_log(f"⚠️ Ошибка диаризации: {str(e)}")
                    smart_log("📝 Продолжаем без диаризации...")
                    result['diarization'] = None
            
            # Сохраняем результат в папку проекта (рядом с видео)
            video_dir = os.path.dirname(downloaded_video_path)
            video_name = os.path.splitext(os.path.basename(downloaded_video_path))[0]
            
            # Пути для сохранения в папке проекта
            transcript_path = os.path.join(video_dir, f"{video_name}_transcript.txt")
            segments_path = os.path.join(video_dir, f"{video_name}_segments.json")
            
            # Формируем текст транскрипции с информацией о спикерах (если есть)
            transcript_text = ""
            if enable_diarization and result.get('diarization'):
                # Формат: [СПИКЕР] текст
                for seg in result['segments']:
                    speaker = seg.get('speaker', 'UNKNOWN')
                    text = seg.get('text', '').strip()
                    if text:
                        transcript_text += f"[{speaker}] {text}\n"
            else:
                # Обычный текст без спикеров
                transcript_text = result['text']
            
            # Сохраняем полный текст транскрипции
            with open(transcript_path, 'w', encoding='utf-8') as f:
                f.write(transcript_text)
            
            # Сохраняем сегменты с временными метками в JSON (включая диаризацию)
            with open(segments_path, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            
            smart_log(f"✅ Транскрипция завершена!")
            smart_log(f"📄 Текст сохранен: {transcript_path}")
            smart_log(f"📊 Сегменты сохранены: {segments_path}")
            smart_log(f"🌍 Язык: {result['language']} (вероятность: {result['language_probability']:.2%})")
            smart_log(f"📝 Всего сегментов: {len(result['segments'])}")
            if enable_diarization and result.get('diarization'):
                smart_log(f"👥 Спикеров: {result['diarization']['total_speakers']}")
            
            ui.notify('Транскрипция завершена!', type='positive')
            
        except Exception as e:
            smart_log(f"❌ Ошибка транскрипции: {str(e)}")
            ui.notify(f'Ошибка: {str(e)}', color='negative')
        finally:
            # Включаем чекбоксы обратно
            if transcribe_checkbox:
                transcribe_checkbox.set_enabled(True)
            if diarize_checkbox:
                diarize_checkbox.set_enabled(True)

    # --- ВЕРСТКА ---
    # value=80 -> Верх 80%, Низ 20%
    with ui.splitter(horizontal=True, value=80).classes('w-full h-[calc(100vh-3.5rem)]') as splitter:

        # === ВЕРХНЯЯ ЧАСТЬ (ТВОЙ ДИЗАЙН) ===
        with splitter.before:
            with ui.tabs().classes('w-full bg-gray-100') as tabs:
                tab_dub = ui.tab('YouTube Дубляж')
                tab_shorts = ui.tab('Shorts Генератор')

            # Возвращаем белый фон и отступы как у тебя было
            with ui.tab_panels(tabs, value=tab_dub).classes('w-full h-full p-0 bg-white overflow-auto'):
                
                with ui.tab_panel(tab_dub).classes('h-full p-0'):
                    # Твоя карточка
                    with ui.card().classes('w-full max-w-2xl mx-auto shadow-none border border-gray-200 p-6 mt-8'):
                        
                        ui.label('Шаг 1: Видео и Качество').classes('text-lg font-bold text-gray-800')
                        
                        # Твой row с инпутами
                        with ui.row().classes('w-full gap-4 items-start no-wrap mt-4'):
                            link_input = ui.input(placeholder='Вставьте ссылку YouTube...').classes('flex-grow text-lg')
                            
                            quality_select = ui.select(
                                options={'max':'Авто (Max)', '2160p':'4K', '1440p':'2K', '1080p':'1080p', '720p':'720p'},
                                value='1080p', label='Качество'
                            ).classes('w-36')

                        ui.separator().classes('my-6')

                        ui.label('Шаг 2: Обработка').classes('text-lg font-bold text-gray-400')
                        with ui.row().classes('w-full gap-4 items-center mt-2'):
                            language_select = ui.select(
                                {'ru': 'Русский', 'en': 'Английский', None: 'Авто'},
                                value=None,
                                label='Язык'
                            ).classes('w-48')
                            ui.checkbox('Клонировать голос').props('disable')
                        
                        with ui.row().classes('w-full gap-4 items-center mt-2'):
                            model_size_select = ui.select(
                                {'tiny': 'Tiny (быстро)', 'base': 'Base (рекомендуется)', 'small': 'Small', 'medium': 'Medium', 'large-v3': 'Large (медленно)'},
                                value='base',
                                label='Модель'
                            ).classes('w-48')
                        
                        # Чекбоксы для обработки
                        transcribe_checkbox = ui.checkbox('Транскрибировать после скачивания', value=False) \
                            .classes('mt-4')
                        
                        diarize_checkbox = ui.checkbox('Диаризация (разделение спикеров)', value=False) \
                            .classes('mt-2')
                        
                        # Поле для Hugging Face token (опционально, для диаризации)
                        with ui.column().classes('w-full mt-2'):
                            hf_token_input = ui.input(
                                label='Hugging Face Token (для диаризации)',
                                placeholder='hf_... (опционально, можно установить в .env)',
                                password=True
                            ).classes('w-full').props('clearable')
                            ui.label('💡 Токен можно установить в .env файл (HF_TOKEN=...)').classes('text-xs text-gray-400 mt-1')
                        
                        ui.button('СКАЧАТЬ ВИДЕО', on_click=start_processing) \
                            .classes('w-full mt-8 h-12 text-lg font-bold text-white shadow-lg') \
                            .props('color=primary')

                with ui.tab_panel(tab_shorts):
                    ui.label('В разработке...').classes('text-gray-500 q-pa-md')

        # === НИЖНЯЯ ЧАСТЬ (ПРОФЕССИОНАЛЬНЫЙ ТЕРМИНАЛ) ===
        with splitter.after:
            # ТЕХНОЛОГИЯ: absolute inset-0
            # Это гарантирует, что терминал займет все место, которое ему выделил сплиттер.
            with ui.element('div').classes('absolute inset-0 flex flex-col bg-[#1e1e1e] border-t border-black overflow-hidden'):
                
                # Шапка терминала
                with ui.row().classes('w-full bg-[#252526] px-2 h-7 items-center justify-between shrink-0'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('terminal', size='14px', color='grey-5')
                        ui.label('TERMINAL OUTPUT').classes('text-[10px] text-gray-400 font-bold font-mono tracking-wider')
                    
                    ui.button(icon='delete', on_click=clear_log) \
                        .props('flat round dense size=xs color=grey') \
                        .tooltip('Очистить')

                # Лог с фиксом min-h-0 и возможностью выделения текста
                log_view = ui.log().classes('flex-1 min-h-0 w-full bg-[#1e1e1e] text-[#4EC9B0] font-mono text-xs p-2 overflow-auto whitespace-pre-wrap leading-tight') \
                    .style('user-select: text !important; -webkit-user-select: text !important; -moz-user-select: text !important; -ms-user-select: text !important;')
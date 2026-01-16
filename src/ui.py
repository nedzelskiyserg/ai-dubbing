from nicegui import ui, run
import core.downloader as downloader
from core.config import APP_PATHS, open_folder 
import asyncio

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
            ::-webkit-scrollbar-thumb:hover { background: #555555; }
            
            /* Стили для textarea лога */
            .log-textarea {
                background-color: #1e1e1e !important;
                color: #4EC9B0 !important;
                border: none !important;
                outline: none !important;
                user-select: text !important;
                -webkit-user-select: text !important;
                -moz-user-select: text !important;
                -ms-user-select: text !important;
                font-family: 'Courier New', 'Consolas', 'Monaco', monospace !important;
                resize: none !important;
            }
            
            .log-textarea::placeholder {
                color: #4EC9B0 !important;
                opacity: 0.5 !important;
            }
            
            /* Убираем стандартные стили Quasar для textarea */
            .log-textarea .q-field__control {
                background-color: #1e1e1e !important;
                color: #4EC9B0 !important;
            }
            
            .log-textarea textarea {
                background-color: #1e1e1e !important;
                color: #4EC9B0 !important;
                border: none !important;
                outline: none !important;
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

    # --- ЛОГИКА ---
    def smart_log(message):
        nonlocal log_view
        if log_view:
            # Добавляем новое сообщение в textarea
            current_text = log_view.value or ''
            log_view.value = current_text + message + '\n'
            # Прокручиваем вниз
            ui.run_javascript(f'''
                var el = getElement({log_view.id});
                if(el) {{
                    el.scrollTop = el.scrollHeight;
                }}
            ''')

    def clear_log():
        nonlocal log_view
        if log_view:
            log_view.value = ''

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
            ui.notify('Готово!', type='positive')
            smart_log(f"✅ СОХРАНЕНО: {result_path}")

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
                            ui.select(['Русский', 'Английский'], value='Русский', label='Язык').classes('w-48').props('disable')
                            ui.checkbox('Клонировать голос').props('disable')

                        ui.button('СКАЧАТЬ ВИДЕО', on_click=start_processing) \
                            .classes('w-full mt-8 h-12 text-lg font-bold text-white shadow-lg') \
                            .props('color=primary')

                with ui.tab_panel(tab_shorts):
                    ui.label('В разработке...').classes('text-gray-500 q-pa-md')

        # === НИЖНЯЯ ЧАСТЬ (ПРОФЕССИОНАЛЬНЫЙ ТЕРМИНАЛ) ===
        with splitter.after:
            # Контейнер терминала с flex-колонкой для правильного растягивания
            with ui.element('div').classes('w-full h-full flex flex-col bg-[#1e1e1e] border-t border-black overflow-hidden'):
                
                # Шапка терминала
                with ui.row().classes('w-full bg-[#252526] px-3 h-8 items-center justify-between shrink-0 border-b border-[#3e3e3e]'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('terminal', size='16px', color='#4EC9B0')
                        ui.label('TERMINAL OUTPUT').classes('text-[11px] text-gray-400 font-bold font-mono tracking-wider uppercase')
                    
                    ui.button(icon='delete', on_click=clear_log) \
                        .props('flat round dense size=sm color=grey') \
                        .classes('text-gray-400 hover:text-white') \
                        .tooltip('Очистить лог')

                # Контейнер для лога с правильным растягиванием
                log_container = ui.element('div').classes('flex-1 min-h-0 w-full overflow-hidden relative')
                
                # Лог с возможностью выделения и копирования текста
                # Используем textarea с правильными стилями для растягивания
                log_view = ui.textarea() \
                    .classes('log-textarea') \
                    .props('readonly filled autogrow') \
                    .style('''
                        position: absolute !important;
                        top: 0 !important;
                        left: 0 !important;
                        right: 0 !important;
                        bottom: 0 !important;
                        width: 100% !important;
                        height: 100% !important;
                        background-color: #1e1e1e !important;
                        color: #4EC9B0 !important;
                        border: none !important;
                        outline: none !important;
                        padding: 12px !important;
                        font-size: 12px !important;
                        line-height: 1.5 !important;
                        font-family: 'Courier New', 'Consolas', 'Monaco', monospace !important;
                        white-space: pre-wrap !important;
                        word-wrap: break-word !important;
                        overflow-y: auto !important;
                        user-select: text !important;
                        -webkit-user-select: text !important;
                        -moz-user-select: text !important;
                        -ms-user-select: text !important;
                        resize: none !important;
                    ''')
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
                    .style('user-select: text; -webkit-user-select: text; -moz-user-select: text; -ms-user-select: text;')
                
                # Применяем стили выделения через JavaScript ко всем дочерним элементам
                ui.run_javascript(f'''
                    setTimeout(function() {{
                        var logEl = getElement({log_view.id});
                        if (logEl) {{
                            logEl.style.userSelect = 'text';
                            logEl.style.webkitUserSelect = 'text';
                            logEl.style.mozUserSelect = 'text';
                            logEl.style.msUserSelect = 'text';
                            // Применяем ко всем дочерним элементам
                            var children = logEl.querySelectorAll('*');
                            for (var i = 0; i < children.length; i++) {{
                                children[i].style.userSelect = 'text';
                                children[i].style.webkitUserSelect = 'text';
                                children[i].style.mozUserSelect = 'text';
                                children[i].style.msUserSelect = 'text';
                            }}
                        }}
                    }}, 100);
                ''')
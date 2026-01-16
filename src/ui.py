from nicegui import ui

def build_interface():
    # -- Стили и настройки страницы --
    ui.colors(primary='#5898d4', secondary='#26a69a', accent='#ea5455', dark='#1d1d1d')
    
    # -- Заголовок --
    with ui.header().classes('items-center justify-between text-white'):
        ui.label('AI Dubbing Studio').classes('text-xl font-bold q-ml-md')
        ui.icon('settings', size='sm').classes('q-mr-md cursor-pointer')

    # -- Основная область с вкладками --
    with ui.tabs().classes('w-full') as tabs:
        tab_dub = ui.tab('YouTube Дубляж')
        tab_shorts = ui.tab('Shorts Генератор')

    with ui.tab_panels(tabs, value=tab_dub).classes('w-full p-4'):
        
        # === Вкладка 1: Дубляж Видео ===
        with ui.tab_panel(tab_dub):
            with ui.card().classes('w-full p-4'):
                ui.label('Шаг 1: Ссылка на видео').classes('text-lg font-bold')
                link_input = ui.input(placeholder='https://youtube.com/...').classes('w-full')
                
                ui.label('Шаг 2: Настройки перевода').classes('text-lg font-bold mt-4')
                with ui.row().classes('w-full gap-4'):
                    target_lang = ui.select(
                        ['Русский', 'Английский', 'Немецкий', 'Испанский', 'Французский'], 
                        value='Русский', label='Целевой язык'
                    ).classes('w-48')
                    
                    ui.checkbox('Клонировать голоса').props('color=primary')
                    ui.checkbox('Вшить субтитры')

                ui.button('НАЧАТЬ ОБРАБОТКУ', on_click=lambda: ui.notify(f'Запуск: {link_input.value}')).classes('w-full mt-6 bg-green-600 text-white')

        # === Вкладка 2: Shorts ===
        with ui.tab_panel(tab_shorts):
            ui.label('Генерация Shorts (В разработке...)').classes('text-gray-500')

    # -- Окно логов (чтобы видеть процесс) --
    with ui.expansion('Лог процесса', icon='list', value=True).classes('w-full border-t mt-auto'):
        log_area = ui.log().classes('w-full h-40 bg-gray-900 text-green-400 font-mono p-2')
        log_area.push('Система готова к работе...')
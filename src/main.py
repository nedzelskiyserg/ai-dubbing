from nicegui import ui

# @ui.page('/') говорит программе: "Это контент для главной страницы"
@ui.page('/')
def index():
    with ui.column().classes('w-full items-center justify-center p-4'):
        ui.label('Привет! Это AI озвучка').classes('text-2xl font-bold text-center')
        ui.label('Версия 3.0 - теперь точно работает!').classes('text-lg text-blue-500')
        
        ui.label('Если ты видишь этот текст, значит мы победили 404.').classes('py-4')
        
        ui.button('Нажми меня', on_click=lambda: ui.notify('Ура, работает!'))

# Точка входа в программу
if __name__ in {"__main__", "__mp_main__"}:
    ui.run(
        native=True,              # Запускать как приложение, а не в браузере
        window_size=(800, 600),   # Размер окна
        title="AI Dubbing App",   # Заголовок
        reload=False,             # ОТКЛЮЧИТЬ перезагрузку (важно для exe)
        port=native.find_open_port() if 'native' in globals() else 8080, # Авто-выбор порта
        storage_secret='secret'   # Ключ шифрования
    )
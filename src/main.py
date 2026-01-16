from nicegui import ui
import sys

def main():
    ui.label('Привет! Это AI озвучка').classes('text-2xl font-bold text-center')
    ui.label('Если ты видишь этот текст - мы победили ошибку 500!').classes('text-lg text-green-500')
    ui.button('Тестовая кнопка', on_click=lambda: ui.notify('Работает!'))

if __name__ in {"__main__", "__mp_main__"}:
    # reload=False ОТКЛЮЧАЕТ поиск изменений в файле (критично для .exe)
    # storage_secret нужен для шифрования сессий (чтобы не ругался)
    ui.run(
        native=True, 
        window_size=(800, 600), 
        title="AI Dubbing App",
        reload=False,
        storage_secret='my-private-key' 
    )
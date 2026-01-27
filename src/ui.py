from nicegui import ui, run
import core.downloader as downloader
from core.transcriber import Transcriber
# from core.diarization import Diarizer, merge_transcription_with_diarization # DELETED
from core.translator import Translator
from core.corrector import SpeakerCorrector
from core.voice_cloner import VoiceCloner
from core.video_maker import VideoMaker
from core.config import APP_PATHS, open_folder 
import asyncio
import os
import json

# Глобальная переменная для хранения загруженного файла (решает проблему области видимости)
_global_uploaded_file_data = None

def build_interface():
    global _global_uploaded_file_data
    # --- 1. CSS СТИЛИ ПО МАКЕТУ PENCIL ---
    ui.add_head_html('''
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&family=IBM+Plex+Mono:wght@400;700&display=swap');
            
            body { 
                margin: 0; 
                padding: 0; 
                overflow: hidden; 
                background: #1A1A1A;
                font-family: 'Space Grotesk', sans-serif;
            }
            .nicegui-content { 
                padding: 0 !important; 
                margin: 0 !important; 
                height: 100vh; 
                width: 100vw; 
                background: #1A1A1A;
            }
            .q-splitter__panel { 
                padding: 0 !important; 
                overflow: hidden !important; 
                position: relative !important; 
            }
            
            /* Скроллбар для терминала */
            ::-webkit-scrollbar { width: 10px; height: 10px; }
            ::-webkit-scrollbar-track { background: #0A0A0A; }
            ::-webkit-scrollbar-thumb { background: #2A2A2A; border-radius: 5px; }
            ::-webkit-scrollbar-thumb:hover { background: #3D3D3D; }
            
            /* Скроллбар для основного контента */
            .nicegui-content ::-webkit-scrollbar { width: 8px; }
            .nicegui-content ::-webkit-scrollbar-track { background: #0F0F0F; }
            .nicegui-content ::-webkit-scrollbar-thumb { background: #2A2A2A; border-radius: 4px; }
            .nicegui-content ::-webkit-scrollbar-thumb:hover { background: #3D3D3D; }
            
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
            
            div[class*="log"],
            pre[class*="log"],
            code[class*="log"] {
                user-select: text !important;
                -webkit-user-select: text !important;
                -moz-user-select: text !important;
                -ms-user-select: text !important;
            }
            
            /* Панели по макету */
            .main-panel {
                background: #0F0F0F;
                border: 3px solid #2A2A2A;
                border-radius: 0;
                width: 100%;
            }
            
            .panel-header {
                height: 56px;
                padding: 0 24px;
                border-bottom: 2px solid #2A2A2A;
                display: flex;
                align-items: center;
                gap: 16px;
            }
            
            .panel-label {
                font-family: 'IBM Plex Mono', monospace;
                font-size: 10px;
                font-weight: normal;
                color: #6B6B6B;
                letter-spacing: 1px;
            }
            
            .panel-title {
                font-family: 'Space Grotesk', sans-serif;
                font-size: 14px;
                font-weight: 700;
                color: #F5F5F0;
                letter-spacing: 1px;
            }
            
            .panel-content {
                padding: 24px;
            }
            
            /* Заголовки секций */
            .section-title {
                font-family: 'Space Grotesk', sans-serif;
                font-size: 12px;
                font-weight: 700;
                color: #F5F5F0;
                letter-spacing: 1px;
            }
            
            /* Кастомные чекбоксы */
            .custom-checkbox {
                color: #F5F5F0;
                font-family: 'Space Grotesk', sans-serif;
                font-size: 11px;
                font-weight: 500;
            }
            
            /* Улучшенные кнопки */
            .btn-primary {
                transition: all 0.2s ease;
                border-radius: 8px;
                font-weight: 600;
                letter-spacing: 0.5px;
            }
            
            .btn-primary:hover {
                transform: translateY(-1px);
                box-shadow: 0 4px 12px rgba(88, 152, 212, 0.3);
            }
            
            .btn-secondary {
                transition: all 0.2s ease;
                border-radius: 8px;
                font-weight: 600;
            }
            
            .btn-secondary:hover {
                transform: translateY(-1px);
                box-shadow: 0 4px 12px rgba(38, 166, 154, 0.3);
            }
            
            .btn-accent {
                transition: all 0.2s ease;
                border-radius: 8px;
                font-weight: 600;
            }
            
            .btn-accent:hover {
                transform: translateY(-1px);
                box-shadow: 0 4px 12px rgba(234, 84, 85, 0.3);
            }
            
            /* Улучшенные инпуты */
            .input-enhanced {
                transition: all 0.2s ease;
                border-radius: 8px;
            }
            
            .input-enhanced:focus {
                border-color: #5898d4;
                box-shadow: 0 0 0 3px rgba(88, 152, 212, 0.1);
            }
            
            /* Иконки в заголовках */
            .step-icon {
                width: 24px;
                height: 24px;
                display: flex;
                align-items: center;
                justify-content: center;
                border-radius: 6px;
                background: linear-gradient(135deg, #5898d4 0%, #4a7fb8 100%);
                color: white;
                font-size: 14px;
            }
            
            /* Подсказки */
            .hint-text {
                color: #6b7280;
                font-size: 12px;
                line-height: 1.5;
                margin-top: 8px;
            }
            
            .hint-text-warning {
                color: #f59e0b;
            }
            
            /* Разделители */
            .divider-enhanced {
                margin: 24px 0;
                border: none;
                border-top: 1px solid #e5e7eb;
            }
            
            /* Чекбоксы и селекты */
            .form-group {
                margin-bottom: 16px;
            }
            
            /* Анимации */
            @keyframes fadeIn {
                from { opacity: 0; transform: translateY(-10px); }
                to { opacity: 1; transform: translateY(0); }
            }
            
            .fade-in {
                animation: fadeIn 0.3s ease;
            }
            
            /* Улучшенные табы */
            .tab-enhanced {
                transition: all 0.2s ease;
            }
            
            /* Контейнер карточек */
            .cards-container {
                padding: 20px;
                max-width: 900px;
                margin: 0 auto;
            }
            
            /* Кастомизация иконки удаления файла в upload - заменяем галочку на крестик/мусорку */
            /* Стилизация кнопки удаления */
            .q-uploader__file-header .q-btn:last-child .q-icon {
                color: #ea5455 !important; /* красный цвет для иконки */
                font-size: 20px !important;
            }
            
            /* Hover эффект для кнопки удаления */
            .q-uploader__file-header .q-btn:last-child:hover .q-icon {
                color: #c62828 !important;
                transform: scale(1.1);
                transition: all 0.2s ease;
            }
        </style>
        <script>
            // Агрессивная замена иконки галочки на крестик/мусорку в upload
            function replaceUploadIcon() {
                // Ищем все кнопки удаления в upload компонентах
                const removeButtons = document.querySelectorAll('.q-uploader__file-header .q-btn:last-child');
                
                removeButtons.forEach(button => {
                    const icon = button.querySelector('.q-icon');
                    if (icon) {
                        // Проверяем, содержит ли иконка классы галочки
                        const hasCheckIcon = icon.classList.contains('mdi-check') || 
                                           icon.classList.contains('mdi-check-circle') || 
                                           icon.classList.contains('mdi-check-circle-outline') ||
                                           icon.textContent.includes('check') ||
                                           icon.innerHTML.includes('check');
                        
                        // Проверяем, не заменена ли уже иконка
                        const isReplaced = icon.classList.contains('icon-replaced') || 
                                         icon.textContent === 'close' || 
                                         icon.textContent === 'delete';
                        
                        if (hasCheckIcon && !isReplaced) {
                            // Удаляем все классы галочки
                            icon.classList.remove('mdi-check', 'mdi-check-circle', 'mdi-check-circle-outline');
                            
                            // Добавляем классы для Material Icons
                            icon.classList.add('material-icons', 'icon-replaced');
                            
                            // Заменяем содержимое на крестик
                            icon.textContent = 'close'; // Крестик
                            // Альтернатива для мусорки: icon.textContent = 'delete';
                            
                            // Устанавливаем стили
                            icon.style.color = '#ea5455';
                            icon.style.fontSize = '20px';
                            icon.style.display = 'inline-block';
                        } else if (!isReplaced && icon.textContent.trim() === '') {
                            // Если иконка пустая, но есть классы, тоже заменяем
                            icon.classList.add('material-icons', 'icon-replaced');
                            icon.textContent = 'close';
                            icon.style.color = '#ea5455';
                            icon.style.fontSize = '20px';
                        }
                    }
                });
            }
            
            // Более агрессивный метод - заменяем через innerHTML
            function replaceUploadIconAggressive() {
                const removeButtons = document.querySelectorAll('.q-uploader__file-header .q-btn:last-child');
                
                removeButtons.forEach(button => {
                    const icon = button.querySelector('.q-icon');
                    if (icon) {
                        // Проверяем, не заменена ли уже
                        if (!icon.classList.contains('icon-replaced')) {
                            // Сохраняем оригинальные классы, но заменяем содержимое
                            const originalClasses = Array.from(icon.classList);
                            
                            // Очищаем классы галочки
                            originalClasses.forEach(cls => {
                                if (cls.includes('check')) {
                                    icon.classList.remove(cls);
                                }
                            });
                            
                            // Добавляем нужные классы
                            if (!icon.classList.contains('material-icons')) {
                                icon.classList.add('material-icons');
                            }
                            icon.classList.add('icon-replaced');
                            
                            // Заменяем содержимое
                            icon.innerHTML = 'close'; // Крестик
                            // Альтернатива для мусорки: icon.innerHTML = 'delete';
                            
                            // Устанавливаем стили
                            icon.style.color = '#ea5455';
                            icon.style.fontSize = '20px';
                            icon.style.display = 'inline-block';
                        }
                    }
                });
            }
            
            // Запускаем замену при загрузке страницы
            function initIconReplacement() {
                replaceUploadIcon();
                replaceUploadIconAggressive();
            }
            
            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', initIconReplacement);
            } else {
                initIconReplacement();
            }
            
            // MutationObserver для отслеживания изменений DOM
            const observer = new MutationObserver(function(mutations) {
                let shouldReplace = false;
                mutations.forEach(function(mutation) {
                    if (mutation.addedNodes.length > 0) {
                        mutation.addedNodes.forEach(function(node) {
                            if (node.nodeType === 1) {
                                // Проверяем, есть ли upload компоненты
                                if (node.classList && (
                                    node.classList.contains('q-uploader') ||
                                    node.classList.contains('q-uploader__file-header') ||
                                    node.querySelector('.q-uploader') ||
                                    node.querySelector('.q-uploader__file-header')
                                )) {
                                    shouldReplace = true;
                                }
                            }
                        });
                    }
                });
                if (shouldReplace) {
                    setTimeout(initIconReplacement, 50);
                }
            });
            
            // Наблюдаем за изменениями в DOM
            observer.observe(document.body, {
                childList: true,
                subtree: true,
                attributes: true,
                attributeFilter: ['class']
            });
            
            // Периодическая проверка (на случай, если MutationObserver пропустит)
            setInterval(initIconReplacement, 500);
            
            // Также запускаем замену через задержки после загрузки
            setTimeout(initIconReplacement, 100);
            setTimeout(initIconReplacement, 300);
            setTimeout(initIconReplacement, 500);
            setTimeout(initIconReplacement, 1000);
            setTimeout(initIconReplacement, 2000);
        </script>
    ''')

    # Цвета по макету Pencil
    ui.colors(primary='#FFD600', secondary='#6B6B6B', accent='#FF6B35', dark='#1A1A1A')

    # --- ХЕДЕР ПО МАКЕТУ PENCIL ---
    with ui.header().classes('items-center justify-between h-16 bg-[#0F0F0F] border-b-[3px] border-[#2A2A2A] px-6'):
        with ui.row().classes('items-center gap-4'):
            # Логотип AI
            with ui.element('div').classes('w-10 h-10 bg-[#FFD600] flex items-center justify-center'):
                ui.label('AI').classes('text-[#1A1A1A] font-bold text-base').style('font-family: "Space Grotesk", sans-serif;')
            
            # Текст заголовка
            with ui.column().classes('gap-0.5'):
                ui.label('[SYS:DUBBING]').classes('text-[#FFD600] text-[10px]').style('font-family: "IBM Plex Mono", monospace; letter-spacing: 1px;')
                ui.label('AI DUBBING STUDIO').classes('text-[#F5F5F0] text-lg font-bold').style('font-family: "Space Grotesk", sans-serif; letter-spacing: 1px;')
        
        # Кнопка папки
        with ui.element('div').classes('w-10 h-10 border-2 border-[#3D3D3D] flex items-center justify-center cursor-pointer hover:border-[#6B6B6B] transition-colors') \
            .on('click', lambda: open_folder(APP_PATHS['downloads'])):
            ui.icon('folder_open', size='24px', color='#F5F5F0')

    # --- СОСТОЯНИЕ ---
    link_input = None
    quality_select = None
    log_view = None
    downloaded_video_path = None
    transcribe_checkbox = None
    diarize_checkbox = None
    correct_speakers_checkbox = None  # Коррекция спикеров через LLм
    num_speakers_input = None
    hf_token_input = None
    model_size_select = None
    language_select = None
    # Переводчик
    translate_checkbox = None
    translate_provider_select = None
    ollama_model_select = None
    translate_target_lang_select = None
    segments_path = None  # Путь к сохраненным сегментам
    # Voice Cloning
    voice_cloning_checkbox = None
    downloaded_video_path_global = None  # Для доступа к исходному видео
    # Локальный файл
    uploaded_file_data = None
    video_source = None
    file_upload = None  # UI элемент для загрузки файла

    # --- ЛОГИКА ---
    def smart_log(message):
        nonlocal log_view
        # Вывод в GUI
        if log_view:
            try:
                log_view.push(message)
                ui.run_javascript(f'var el = getElement({log_view.id}); if(el) el.scrollTop = el.scrollHeight;')
            except RuntimeError:
                # Клиент был удален (страница перезагружена), игнорируем ошибку GUI
                pass
            except Exception as e:
                print(f"Ошибка обновления GUI лога: {e}", flush=True)
        
        # Вывод в системный терминал (для отладки)
        try:
            print(message, flush=True)
        except Exception:
            pass  # Игнорируем ошибки вывода в консоль (например, если нет консоли)

    def clear_log():
        nonlocal log_view
        if log_view:
            log_view.clear()

    async def start_processing():
        # КРИТИЧНО: Используем nonlocal для доступа к переменным из build_interface
        nonlocal uploaded_file_data, video_source, file_upload
        
        # Отладка
        smart_log(f"🔍 Отладка start_processing: video_source = {video_source}")
        smart_log(f"🔍 Отладка start_processing: uploaded_file_data = {uploaded_file_data}")
        if uploaded_file_data:
            smart_log(f"🔍 Отладка start_processing: uploaded_file_data.name = {uploaded_file_data.name if hasattr(uploaded_file_data, 'name') else 'N/A'}")
        
        if not video_source:
            ui.notify('Ошибка: Источник видео не определен!', color='negative')
            return
        
        if video_source.value == 'YouTube':
            # Обработка YouTube
            url = link_input.value
            quality = quality_select.value 
            if not url:
                ui.notify('Ошибка: Введите ссылку!', color='negative')
                return
            
            smart_log(f"\n🚀 ЗАПУСК: {url} [{quality}]")
            smart_log("─" * 40)
            
            result_path = await run.io_bound(downloader.download_video, url, smart_log, quality)
        else:
            # Обработка локального файла
            # КРИТИЧНО: Используем глобальную переменную как приоритетный источник
            global _global_uploaded_file_data
            current_file_data = _global_uploaded_file_data or uploaded_file_data
            
            smart_log(f"🔍 Проверка перед обработкой:")
            smart_log(f"   uploaded_file_data (локальная) = {uploaded_file_data}")
            smart_log(f"   _global_uploaded_file_data (глобальная) = {_global_uploaded_file_data}")
            smart_log(f"   current_file_data (используется) = {current_file_data}")
            
            if not current_file_data:
                ui.notify('Ошибка: Выберите видео файл!', color='negative')
                smart_log(f"❌ Файл не выбран.")
                smart_log(f"💡 Убедитесь, что файл загружен и появилось сообщение '✅ Файл выбран'")
                return
            
            # Используем current_file_data
            uploaded_file_data = current_file_data
            
            smart_log(f"\n📁 ИМПОРТ ЛОКАЛЬНОГО ФАЙЛА")
            smart_log("─" * 40)
            
            result_path = await run.io_bound(process_local_file, uploaded_file_data, smart_log)
        
        if result_path:
            nonlocal downloaded_video_path, downloaded_video_path_global
            downloaded_video_path = result_path
            downloaded_video_path_global = result_path  # Сохраняем для voice cloning
            ui.notify('Готово!', type='positive')
            smart_log(f"✅ СОХРАНЕНО: {result_path}")
            
            # Если чекбокс транскрипции включен, запускаем автоматически
            if transcribe_checkbox and transcribe_checkbox.value:
                smart_log(f"📝 Автоматический запуск транскрипции...")
                await start_transcription()
            else:
                smart_log(f"📝 Видео готово. Включите 'Транскрибировать после скачивания' для автоматической транскрипции.")
    
    def process_local_file(uploaded_file, log_func):
        """
        Обрабатывает загруженный локальный файл:
        1. Сохраняет его во временную директорию
        2. Копирует в структуру downloads (как YouTube)
        3. Возвращает путь к файлу
        """
        import shutil
        from pathlib import Path
        from core.config import APP_PATHS
        
        try:
            # Получаем имя файла и объект содержимого
            if hasattr(uploaded_file, 'name'):
                original_filename = uploaded_file.name
                content_obj = uploaded_file.content if hasattr(uploaded_file, 'content') else None
            else:
                original_filename = 'video.mp4'
                content_obj = uploaded_file if hasattr(uploaded_file, 'read') else None
            
            if content_obj is None:
                log_func(f"❌ Ошибка: не удалось получить содержимое файла")
                return None
            
            file_extension = Path(original_filename).suffix.lower()
            video_name = Path(original_filename).stem
            
            # Проверяем, что это видео файл
            video_extensions = ['.mp4', '.avi', '.mov', '.mkv', '.webm', '.flv', '.wmv', '.m4v', '.3gp', '.ogv']
            if file_extension and file_extension not in video_extensions:
                log_func(f"⚠️ Предупреждение: расширение файла '{file_extension}' не является стандартным видео форматом")
                log_func(f"   Продолжаем обработку...")
            
            if not file_extension:
                file_extension = '.mp4'
                log_func(f"⚠️ Расширение файла не определено, используем .mp4")
            
            log_func(f"📂 Исходный файл: {original_filename}")
            
            # Создаем папку для проекта (как в downloader)
            output_folder = APP_PATHS["downloads"]
            video_folder_name = f"{video_name}_local"
            video_folder = os.path.join(output_folder, video_folder_name)
            os.makedirs(video_folder, exist_ok=True)
            log_func(f"📁 Создана папка проекта: {video_folder_name}")
            
            # Путь для сохранения
            final_filename = f"{video_name}{file_extension}"
            final_path = os.path.join(video_folder, final_filename)
            
            # Сохраняем файл
            log_func(f"💾 Сохранение файла...")
            
            # Читаем содержимое из BytesIO объекта и записываем в файл
            try:
                # Сбрасываем позицию чтения на начало (если уже читали)
                if hasattr(content_obj, 'seek'):
                    content_obj.seek(0)
                
                # Читаем все содержимое
                file_content = content_obj.read()
                
                # Проверяем, что это bytes, а не корутина
                if not isinstance(file_content, bytes):
                    log_func(f"❌ Ошибка: content_obj.read() вернул не bytes, а {type(file_content)}")
                    return None
                
                # Записываем в файл
                with open(final_path, 'wb') as f:
                    f.write(file_content)
                    
            except Exception as read_error:
                log_func(f"❌ Ошибка чтения/записи файла: {str(read_error)}")
                import traceback
                log_func(f"📋 Детали: {traceback.format_exc()}")
                return None
            
            # Проверяем размер файла
            file_size_mb = os.path.getsize(final_path) / (1024 * 1024)
            log_func(f"✅ Файл сохранен: {final_filename} ({file_size_mb:.1f} MB)")
            
            return final_path
            
        except Exception as e:
            log_func(f"❌ Ошибка обработки файла: {str(e)}")
            import traceback
            log_func(f"📋 Детали: {traceback.format_exc()}")
            return None
    
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
        num_speakers = num_speakers_input.value if num_speakers_input else None
        
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
        if enable_diarization and num_speakers:
            smart_log(f"🔢 Кол-во спикеров: {num_speakers}")
        
        # Словарь промптов для улучшения качества
        initial_prompts = {
            'ru': "Вот текст на русском языке. Раздели его на предложения, используй правильную пунктуацию и заглавные буквы.",
            'en': "Here is the English text. Split it into sentences, use proper punctuation and capitalization.",
        }
        
        # Выбираем промпт в зависимости от языка
        # Если язык не выбран (Авто), используем промпт для русского как наиболее вероятного или универсальный
        prompt = initial_prompts.get(language, initial_prompts.get('ru'))
        
        try:
            # Создаем транскрибер с callback для прогресса и токеном
            transcriber = Transcriber(
                model_size=model_size,
                hf_token=hf_token,
                progress_callback=smart_log
            )
            
            # Запускаем полный пайплайн транскрипции
            result = await run.io_bound(
                transcriber.transcribe_full,
                downloaded_video_path,
                language=language,
                num_speakers=num_speakers
            )
            
            # Извлекаем данные из результата
            result_segments = result.get("segments", [])
            detected_language = result.get("language", language or "не определен")
            
            # КОРРЕКЦИЯ СПИКЕРОВ (опционально, через LLM)
            enable_correction = correct_speakers_checkbox.value if correct_speakers_checkbox else False
            if enable_correction and result_segments:
                smart_log(f"\n🔧 Коррекция спикеров через LLM...")
                try:
                    # Получаем модель Ollama из настроек переводчика (если есть)
                    ollama_model = "qwen2.5:7b"  # По умолчанию
                    if ollama_model_select and ollama_model_select.value:
                        ollama_model = ollama_model_select.value
                    
                    corrector = SpeakerCorrector(
                        ollama_url="http://localhost:11434",
                        model=ollama_model,
                        progress_callback=smart_log
                    )
                    
                    result_segments_before = len(result_segments)
                    result_segments = await run.io_bound(corrector.correct, result_segments)
                    result_segments_after = len(result_segments)
                    smart_log(f"✅ Коррекция спикеров завершена: {result_segments_before} → {result_segments_after} сегментов")
                    
                    # ОТЛАДКА: Показываем примеры исправленных сегментов
                    if result_segments:
                        smart_log(f"📋 Примеры исправленных сегментов (первые 3):")
                        for i, seg in enumerate(result_segments[:3]):
                            speaker = seg.get('speaker', 'UNKNOWN')
                            text = seg.get('text', '')[:50] + '...' if len(seg.get('text', '')) > 50 else seg.get('text', '')
                            smart_log(f"   [{speaker}] {text}")
                except Exception as e:
                    smart_log(f"⚠️ Ошибка коррекции спикеров: {e}")
                    smart_log(f"💡 Продолжаем без коррекции...")
                    # Продолжаем с оригинальными сегментами
            
            # Сохраняем результат в папку проекта (рядом с видео)
            video_dir = os.path.dirname(downloaded_video_path)
            video_name = os.path.splitext(os.path.basename(downloaded_video_path))[0]
            
            # Пути для сохранения в папке проекта
            transcript_path = os.path.join(video_dir, f"{video_name}_transcript.txt")
            local_segments_path = os.path.join(video_dir, f"{video_name}_segments.json")
            
            # Формируем текст транскрипции (сценарий)
            transcript_text = "СЦЕНАРИЙ (WHISPERX PIPELINE)\n"
            transcript_text += "=" * 50 + "\n\n"
            
            current_speaker = None
            speakers_set = set()
            
            def format_timestamp(seconds):
                m, s = divmod(seconds, 60)
                h, m = divmod(m, 60)
                return f"{int(h):02d}:{int(m):02d}:{int(s):02d}"

            for seg in result_segments:
                speaker = seg.get('speaker', 'SPEAKER_UNKNOWN')
                text = seg.get('text', '').strip()
                start = seg.get('start', 0.0)
                end = seg.get('end', 0.0)
                
                if not text:
                    continue
                    
                speakers_set.add(speaker)
                
                if speaker != current_speaker:
                    if current_speaker is not None:
                        transcript_text += "\n\n"
                    
                    time_range = f"[{format_timestamp(start)} -> {format_timestamp(end)}]"
                    transcript_text += f"👤 {speaker} {time_range}:\n"
                    current_speaker = speaker
                
                transcript_text += f"{text} "
            
            # Статистика
            transcript_text += "\n\n" + "=" * 50 + "\n"
            transcript_text += f"📊 СТАТИСТИКА:\n"
            transcript_text += f"- Всего спикеров: {len(speakers_set)}\n"
            transcript_text += f"- Список: {', '.join(sorted(speakers_set))}\n"

            # ОТЛАДКА: Проверяем, что исправленные сегменты действительно используются
            if enable_correction:
                smart_log(f"🔍 Проверка перед сохранением: {len(result_segments)} сегментов с исправленными спикерами")
                # Показываем примеры спикеров в исправленных сегментах
                speakers_in_result = set(seg.get('speaker', 'UNKNOWN') for seg in result_segments)
                smart_log(f"   📊 Спикеры в исправленных сегментах: {sorted(speakers_in_result)}")
            
            # Сохраняем полный текст транскрипции
            with open(transcript_path, 'w', encoding='utf-8') as f:
                f.write(transcript_text)
            
            # Сохраняем JSON (для перевода и истории)
            # Оборачиваем список сегментов в структуру, которую ожидает остальной код
            full_result_json = {
                "segments": result_segments,  # ВАЖНО: Используем исправленные сегменты
                "language": detected_language,
                "language_probability": 0.99,  # WhisperX не возвращает вероятность, ставим дефолт
                "diarization": {
                    "total_speakers": len(speakers_set),
                    "speakers": sorted(list(speakers_set))
                }
            }
            
            with open(local_segments_path, 'w', encoding='utf-8') as f:
                json.dump(full_result_json, f, ensure_ascii=False, indent=2)
            
            # ОТЛАДКА: Проверяем, что файл действительно содержит исправленные данные
            if enable_correction:
                smart_log(f"✅ Файлы сохранены с исправленными сегментами")
                smart_log(f"   📄 TXT: {transcript_path}")
                smart_log(f"   📊 JSON: {local_segments_path}")
            
            # Сохраняем путь к сегментам для перевода
            nonlocal segments_path
            segments_path = local_segments_path
            
            smart_log(f"✅ Транскрипция завершена!")
            smart_log(f"📄 Текст сохранен: {transcript_path}")
            smart_log(f"📊 Сегменты сохранены: {local_segments_path}")
            smart_log(f"🌍 Язык: {detected_language}")
            smart_log(f"📝 Всего сегментов: {len(result_segments)}")
            if enable_diarization and len(speakers_set) > 0:
                smart_log(f"👥 Спикеров: {full_result_json['diarization']['total_speakers']}")
            
            # Показываем уведомление
            try:
                ui.notify('Транскрипция завершена!', type='positive')
            except RuntimeError:
                pass
            
            # Если чекбокс перевода включен, запускаем автоматически
            if translate_checkbox and translate_checkbox.value:
                smart_log(f"🌐 Автоматический запуск перевода...")
                await start_translation()
                
        except Exception as e:
            smart_log(f"❌ Ошибка транскрипции: {str(e)}")
            try:
                ui.notify(f'Ошибка: {str(e)}', color='negative')
            except RuntimeError:
                pass
            
        except Exception as e:
            smart_log(f"❌ Ошибка транскрипции: {str(e)}")
            # Показываем уведомление об ошибке, если клиент еще активен
            try:
                ui.notify(f'Ошибка: {str(e)}', color='negative')
            except RuntimeError:
                # Клиент был удален, игнорируем
                pass
        finally:
            # Включаем чекбоксы обратно
            if transcribe_checkbox:
                transcribe_checkbox.set_enabled(True)
            if diarize_checkbox:
                diarize_checkbox.set_enabled(True)
    
    async def start_translation():
        """Запускает перевод сегментов транскрипции"""
        nonlocal segments_path
        
        if not segments_path or not os.path.exists(segments_path):
            ui.notify('Ошибка: Сначала выполните транскрипцию!', color='negative')
            return
        
        # Получаем настройки перевода
        provider = translate_provider_select.value if translate_provider_select else "api"
        target_lang = translate_target_lang_select.value if translate_target_lang_select else "ru"
        model = ollama_model_select.value if ollama_model_select else "qwen2.5:7b"
        
        # Отключаем чекбокс во время обработки
        if translate_checkbox:
            translate_checkbox.set_enabled(False)
        
        smart_log(f"\n🌐 ЗАПУСК ПЕРЕВОДА")
        smart_log("─" * 40)
        smart_log(f"📁 Файл сегментов: {os.path.basename(segments_path)}")
        smart_log(f"🔧 Провайдер: {provider}")
        if provider == "ollama":
            smart_log(f"🤖 Модель: {model}")
        else:
            smart_log(f"🌐 Используется качественный API (DeepL → LibreTranslate → MyMemory → Google)")
        smart_log(f"🌍 Целевой язык: {target_lang}")
        
        try:
            # Загружаем сегменты
            with open(segments_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            segments = data.get('segments', [])
            if not segments:
                raise ValueError("Сегменты не найдены в файле")
            
            smart_log(f"📝 Загружено сегментов: {len(segments)}")
            
            # Создаем переводчик
            translator = Translator(progress_callback=smart_log)
            
            # Определяем исходный язык
            source_lang = data.get('language', 'en')
            
            # Выбираем провайдера
            # Если выбран API, принудительно используем качественный API
            # Если выбран Ollama, используем Ollama (если доступен)
            force_fallback = (provider == "api")  # API = принудительный fallback (качественный)
            use_fallback = True  # Всегда разрешаем fallback как резерв
            
            # Запускаем перевод в executor
            translated_segments = await run.io_bound(
                translator.translate_segments,
                segments,
                target_lang=target_lang,
                source_lang=source_lang,
                model=model,
                use_fallback=use_fallback,
                force_fallback=force_fallback,
                batch_size=1
            )
            
            # Обновляем данные с переведенными сегментами
            data['segments'] = translated_segments
            data['translated_language'] = target_lang
            data['original_language'] = source_lang
            
            # Сохраняем переведенные сегменты
            video_dir = os.path.dirname(segments_path)
            video_name = os.path.splitext(os.path.basename(segments_path))[0].replace('_segments', '')
            translated_segments_path = os.path.join(video_dir, f"{video_name}_translated_{target_lang}_segments.json")
            
            with open(translated_segments_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            # Формируем переведенный текст
            translated_text = ""
            if data.get('diarization'):
                # Формат: [СПИКЕР] текст
                for seg in translated_segments:
                    speaker = seg.get('speaker', 'UNKNOWN')
                    text = seg.get('text', '').strip()
                    if text:
                        translated_text += f"[{speaker}] {text}\n"
            else:
                # Обычный текст без спикеров
                translated_text = "\n".join([seg.get('text', '') for seg in translated_segments])
            
            # Сохраняем переведенный текст
            translated_transcript_path = os.path.join(video_dir, f"{video_name}_translated_{target_lang}_transcript.txt")
            with open(translated_transcript_path, 'w', encoding='utf-8') as f:
                f.write(translated_text)
            
            smart_log(f"✅ Перевод завершен!")
            smart_log(f"📄 Переведенный текст: {translated_transcript_path}")
            smart_log(f"📊 Переведенные сегменты: {translated_segments_path}")
            
            # Если включен voice cloning, запускаем автоматически
            if voice_cloning_checkbox and voice_cloning_checkbox.value:
                smart_log(f"🎤 Автоматический запуск клонирования голоса...")
                await start_voice_cloning(translated_segments_path, target_lang)
            
            # Показываем уведомление
            try:
                ui.notify('Перевод завершен!', type='positive')
            except RuntimeError:
                pass
            
        except Exception as e:
            smart_log(f"❌ Ошибка перевода: {str(e)}")
            try:
                ui.notify(f'Ошибка: {str(e)}', color='negative')
            except RuntimeError:
                pass
        finally:
            # Включаем чекбокс обратно
            if translate_checkbox:
                translate_checkbox.set_enabled(True)
    
    async def start_voice_cloning(translated_segments_path: str, target_lang: str):
        """
        Запускает процесс клонирования голоса и создания переозвучки.
        
        Процесс:
        1. Извлечение референсных аудио для каждого спикера из исходного видео
        2. Генерация аудио для каждого переведенного сегмента с клонированием голоса
        3. Объединение всех аудио сегментов в финальный файл
        """
        nonlocal downloaded_video_path
        
        if not translated_segments_path or not os.path.exists(translated_segments_path):
            smart_log("❌ Ошибка: Файл с переведенными сегментами не найден!")
            return
        
        if not downloaded_video_path or not os.path.exists(downloaded_video_path):
            smart_log("❌ Ошибка: Исходное видео не найдено! Нужно для извлечения референсных аудио.")
            return
        
        # Отключаем чекбокс во время обработки
        if voice_cloning_checkbox:
            voice_cloning_checkbox.set_enabled(False)
        
        try:
            smart_log(f"\n🎤 ЗАПУСК КЛОНИРОВАНИЯ ГОЛОСА И ПЕРЕОЗВУЧКИ")
            smart_log("─" * 50)
            
            # Загружаем переведенные сегменты
            with open(translated_segments_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            segments = data.get('segments', [])
            if not segments:
                raise ValueError("Сегменты не найдены в файле")
            
            smart_log(f"📝 Загружено сегментов: {len(segments)}")
            
            # Создаем VoiceCloner
            cloner = VoiceCloner(progress_callback=smart_log)
            
            # ШАГ 1: Извлечение референсных аудио для каждого спикера
            smart_log(f"\n🎯 Шаг 1/3: Извлечение референсных аудио...")
            speaker_samples = await run.io_bound(
                cloner.extract_speaker_samples,
                downloaded_video_path,
                segments
            )
            
            if not speaker_samples:
                raise ValueError("Не удалось извлечь референсные аудио для спикеров")
            
            smart_log(f"✅ Референсные аудио извлечены для {len(speaker_samples)} спикеров")
            
            # ШАГ 2: Генерация аудио для каждого сегмента
            smart_log(f"\n🎬 Шаг 2/3: Генерация аудио с клонированием голоса...")
            segments_with_audio = await run.io_bound(
                cloner.generate_dubbing,
                segments,
                speaker_samples,
                target_lang
            )
            
            # ШАГ 3: Объединение всех аудио сегментов (опционально, для аудио-файла)
            smart_log(f"\n🔗 Шаг 3/4: Объединение аудио сегментов...")
            
            video_dir = os.path.dirname(translated_segments_path)
            video_name = os.path.splitext(os.path.basename(translated_segments_path))[0].replace('_translated_', '_').replace('_segments', '')
            final_audio_path = os.path.join(video_dir, f"{video_name}_dubbed.wav")
            
            final_audio_file = await run.io_bound(
                cloner.merge_audio_segments,
                segments_with_audio,
                final_audio_path
            )
            
            smart_log(f"✅ Аудио объединено: {final_audio_file}")
            
            # ШАГ 4: Создание финального видео с дубляжом
            smart_log(f"\n🎬 Шаг 4/4: Создание финального видео...")
            
            video_maker = VideoMaker(progress_callback=smart_log)
            final_video_path = os.path.join(video_dir, f"{video_name}_dubbed.mp4")
            
            final_video_file = await run.io_bound(
                video_maker.make_video,
                downloaded_video_path,
                segments_with_audio,
                final_video_path
            )
            
            smart_log(f"\n✅ ПЕРЕОЗВУЧКА ЗАВЕРШЕНА!")
            smart_log(f"🎬 Финальное видео: {final_video_file}")
            smart_log(f"🎵 Финальное аудио: {final_audio_file}")
            
            # Сохраняем информацию о переозвучке
            dubbing_info = {
                "original_video": downloaded_video_path,
                "translated_segments": translated_segments_path,
                "final_audio": final_audio_file,
                "final_video": final_video_file,
                "target_language": target_lang,
                "speaker_samples": speaker_samples,
                "segments_count": len(segments_with_audio)
            }
            
            info_path = os.path.join(video_dir, f"{video_name}_dubbing_info.json")
            with open(info_path, 'w', encoding='utf-8') as f:
                json.dump(dubbing_info, f, ensure_ascii=False, indent=2)
            
            smart_log(f"📊 Информация о переозвучке: {info_path}")
            
            # Очищаем временные файлы
            video_maker.cleanup_temp_files()
            
            # Показываем уведомление
            try:
                ui.notify('Переозвучка создана!', type='positive')
            except RuntimeError:
                pass
            
        except ImportError as e:
            error_msg = str(e)
            if "TTS" in error_msg or "Python 3.10" in error_msg:
                smart_log(f"❌ {error_msg}")
                smart_log(f"💡 Запустите: ./setup_voice_cloning.sh")
            else:
                smart_log(f"❌ Ошибка импорта: {error_msg}")
        except Exception as e:
            smart_log(f"❌ Ошибка клонирования голоса: {str(e)}")
            import traceback
            smart_log(f"📋 Детали: {traceback.format_exc()}")
            try:
                ui.notify(f'Ошибка: {str(e)}', color='negative')
            except RuntimeError:
                pass
        finally:
            # Включаем чекбокс обратно
            if voice_cloning_checkbox:
                voice_cloning_checkbox.set_enabled(True)

    # --- ВЕРСТКА ПО МАКЕТУ PENCIL ---
    # value=80 -> Верх 80%, Низ 20%
    with ui.splitter(horizontal=True, value=80).classes('w-full h-[calc(100vh-4rem)] bg-[#1A1A1A]') as splitter:

        # === ВЕРХНЯЯ ЧАСТЬ (ОСНОВНОЙ КОНТЕНТ ПО МАКЕТУ) ===
        with splitter.before:
            # Основной контейнер
            with ui.column().classes('w-full h-full bg-[#1A1A1A] p-10 gap-6 overflow-auto max-w-[1400px] mx-auto'):
                
                # ПАНЕЛЬ 1: VIDEO SOURCE
                with ui.card().classes('main-panel'):
                    # Заголовок панели
                    with ui.row().classes('panel-header'):
                        ui.label('[INPUT:SOURCE]').classes('panel-label')
                        ui.label('VIDEO SOURCE').classes('panel-title')
                        ui.icon('video_library', size='20px', color='#FFD600')
                    
                    # Содержимое панели
                    with ui.column().classes('panel-content'):
                        # YouTube URL секция
                        with ui.column().classes('w-full gap-3'):
                            ui.label('PASTE YOUTUBE URL').classes('text-[#6B6B6B] text-[11px] font-bold').style('font-family: "Space Grotesk", sans-serif; letter-spacing: 1px;')
                            
                            # Контейнер для YouTube опций
                            youtube_container = ui.column().classes('w-full')
                            with youtube_container:
                                with ui.row().classes('w-full gap-4'):
                                    link_input = ui.input(
                                        placeholder='https://youtube.com/watch?v=...',
                                        value='https://www.youtube.com/shorts/eqSciL0d7wc'
                                    ).classes('flex-grow').style('background: #1A1A1A; border: 2px solid #2A2A2A; color: #4D4D4D; font-family: "IBM Plex Mono", monospace; font-size: 13px; padding: 0 16px; height: 52px;')
                                    
                                    quality_select = ui.select(
                                        options={'max':'Авто (Max)', '2160p':'4K', '1440p':'2K', '1080p':'1080P', '720p':'720P'},
                                        value='1080p', label=''
                                    ).classes('w-[140px]').style('background: #1A1A1A; border: 2px solid #2A2A2A; color: #F5F5F0; font-family: "Space Grotesk", sans-serif; font-size: 12px; font-weight: 700; height: 52px;')
                        
                        # Разделитель OR
                        with ui.row().classes('w-full items-center gap-4 my-6'):
                            ui.element('div').classes('flex-1 h-[2px] bg-[#2A2A2A]')
                            ui.label('OR').classes('text-[#4D4D4D] text-[11px] font-bold').style('font-family: "Space Grotesk", sans-serif; letter-spacing: 1px;')
                            ui.element('div').classes('flex-1 h-[2px] bg-[#2A2A2A]')
                        
                        # Drag & Drop зона
                        # Контейнер для локального файла
                        local_file_container = ui.column().classes('w-full')
                        
                        with local_file_container:
                            with ui.element('div').classes('w-full h-[180px] bg-[#0A0A0A] border-2 border-dashed border-[#3D3D3D] flex flex-col items-center justify-center gap-4 cursor-pointer hover:border-[#6B6B6B] transition-colors') \
                                .on('click', lambda: file_upload.click() if file_upload else None):
                                ui.icon('upload_file', size='48px', color='#4D4D4D')
                                ui.label('DRAG & DROP VIDEO FILE').classes('text-[#6B6B6B] text-sm font-bold').style('font-family: "Space Grotesk", sans-serif; letter-spacing: 1px;')
                                ui.label('or click to browse').classes('text-[#4D4D4D] text-xs').style('font-family: "IBM Plex Mono", monospace;')
                                
                                with ui.row().classes('items-center gap-2 mt-2'):
                                    ui.label('[FORMATS]').classes('text-[#3D3D3D] text-[10px] font-bold').style('font-family: "IBM Plex Mono", monospace; letter-spacing: 1px;')
                                    ui.label('MP4 • MKV • AVI • MOV • WEBM').classes('text-[#4D4D4D] text-[10px]').style('font-family: "IBM Plex Mono", monospace;')
                            
                            local_file_info = ui.label('').classes('text-[#FFD600] text-xs mt-2')
                            
                            # Используем замыкание для доступа к uploaded_file_data
                            async def handle_upload(e):
                                    global _global_uploaded_file_data
                                    nonlocal uploaded_file_data
                                    
                                    # ОТЛАДКА: Логируем все атрибуты объекта e
                                    print(f"🔔 handle_upload ВЫЗВАН!")
                                    print(f"🔍 Тип e: {type(e)}")
                                    print(f"🔍 Все атрибуты e: {[attr for attr in dir(e) if not attr.startswith('_')]}")
                                    
                                    try:
                                        smart_log(f"🔔 handle_upload вызван!")
                                        
                                        # NiceGUI передает UploadEventArguments
                                        # Из логов видно, что атрибуты: ['client', 'file', 'sender']
                                        # Файл передается через e.file!
                                        file_name = None
                                        file_content_obj = None
                                        
                                        # Получаем файл через e.file (основной способ в NiceGUI)
                                        if hasattr(e, 'file') and e.file:
                                            file_obj = e.file
                                        print(f"🔍 e.file тип: {type(file_obj)}")
                                        print(f"🔍 e.file атрибуты: {[attr for attr in dir(file_obj) if not attr.startswith('_')]}")
                                        
                                        # Получаем имя файла
                                        if hasattr(file_obj, 'name') and file_obj.name:
                                            file_name = file_obj.name
                                            print(f"📎 Имя файла из e.file.name: {file_name}")
                                        elif hasattr(file_obj, 'filename') and file_obj.filename:
                                            file_name = file_obj.filename
                                            print(f"📎 Имя файла из e.file.filename: {file_name}")
                                        
                                        # Получаем содержимое файла
                                        # LargeFileUpload.read() может возвращать корутину (async)
                                        if hasattr(file_obj, 'read'):
                                            try:
                                                # Пробуем прочитать (может быть корутиной)
                                                read_result = file_obj.read()
                                                
                                                # Проверяем, корутина ли это
                                                if hasattr(read_result, '__await__'):
                                                    # Это корутина, нужно await
                                                    file_bytes = await read_result
                                                    print(f"✅ Файл прочитан через await: {len(file_bytes)} байт")
                                                elif isinstance(read_result, bytes):
                                                    # Это уже байты
                                                    file_bytes = read_result
                                                    print(f"✅ Файл прочитан напрямую: {len(file_bytes)} байт")
                                                else:
                                                    # Что-то другое, пробуем использовать как есть
                                                    file_bytes = read_result
                                                
                                                # Создаем BytesIO из прочитанных байтов
                                                from io import BytesIO
                                                file_content_obj = BytesIO(file_bytes)
                                                file_content_obj.seek(0)
                                                print(f"✅ BytesIO создан: {len(file_bytes)} байт ({len(file_bytes) / (1024*1024):.2f} MB)")
                                            except Exception as read_err:
                                                print(f"❌ Ошибка чтения e.file.read(): {read_err}")
                                                import traceback
                                                print(f"📋 Детали: {traceback.format_exc()}")
                                                # Пробуем использовать save() для сохранения во временный файл
                                                try:
                                                    import tempfile
                                                    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.mp4')
                                                    # save() тоже может быть async
                                                    save_result = file_obj.save(temp_file.name)
                                                    if hasattr(save_result, '__await__'):
                                                        await save_result
                                                    # Читаем из временного файла
                                                    with open(temp_file.name, 'rb') as f:
                                                        file_bytes = f.read()
                                                    from io import BytesIO
                                                    file_content_obj = BytesIO(file_bytes)
                                                    file_content_obj.seek(0)
                                                    print(f"✅ Файл сохранен и прочитан через save(): {len(file_bytes)} байт")
                                                    # Удаляем временный файл после чтения
                                                    import os
                                                    os.unlink(temp_file.name)
                                                except Exception as save_err:
                                                    print(f"❌ Ошибка save(): {save_err}")
                                                    import traceback
                                                    print(f"📋 Детали save: {traceback.format_exc()}")
                                                    file_content_obj = None
                                        elif hasattr(file_obj, 'content'):
                                            # Если есть атрибут content
                                            content_attr = file_obj.content
                                            if hasattr(content_attr, 'read'):
                                                try:
                                                    # Читаем без seek
                                                    file_bytes = content_attr.read()
                                                    from io import BytesIO
                                                    file_content_obj = BytesIO(file_bytes)
                                                    file_content_obj.seek(0)
                                                    print(f"✅ Файл прочитан из e.file.content: {len(file_bytes)} байт")
                                                except Exception as read_err:
                                                    print(f"❌ Ошибка чтения e.file.content: {read_err}")
                                                    file_content_obj = content_attr
                                        
                                        # Fallback: пробуем e.content (старый способ)
                                        if not file_content_obj and hasattr(e, 'content') and e.content:
                                            content_attr = e.content
                                            print(f"🔍 Fallback: e.content тип: {type(content_attr)}")
                                            
                                            if hasattr(content_attr, 'read'):
                                                try:
                                                    # Читаем без seek (если это LargeFileUpload)
                                                    if hasattr(content_attr, 'seek'):
                                                        content_attr.seek(0)
                                                    file_bytes = content_attr.read()
                                                    from io import BytesIO
                                                    file_content_obj = BytesIO(file_bytes)
                                                    file_content_obj.seek(0)
                                                    print(f"✅ Файл прочитан из e.content: {len(file_bytes)} байт")
                                                except Exception as read_err:
                                                    print(f"❌ Ошибка чтения e.content: {read_err}")
                                                    file_content_obj = content_attr
                                        
                                        # Fallback: получаем имя файла из других источников
                                        if not file_name:
                                            if hasattr(e, 'name') and e.name:
                                                file_name = e.name
                                            elif hasattr(e, 'filename') and e.filename:
                                                file_name = e.filename
                                        
                                        # Если имя не найдено, используем дефолтное
                                        if not file_name:
                                            file_name = 'video.mp4'
                                        
                                        print(f"📎 Результат: file_name = {file_name}, content_obj = {file_content_obj is not None}")
                                        smart_log(f"📎 Получен файл: {file_name}, content_obj = {file_content_obj is not None}")
                                        
                                        if file_content_obj is None:
                                            error_msg = '⚠️ Ошибка: содержимое файла не получено. Попробуйте другой файл.'
                                            print(error_msg)
                                            smart_log(error_msg)
                                            local_file_info.text = error_msg
                                            local_file_info.classes('mt-4 text-red-600')
                                            return
                                        
                                        # КРИТИЧНО: Убеждаемся, что file_content_obj это BytesIO (не LargeFileUpload)
                                        # Если это еще не BytesIO, создаем его
                                        from io import BytesIO
                                        if not isinstance(file_content_obj, BytesIO):
                                            try:
                                                # Читаем содержимое (без seek, если это LargeFileUpload)
                                                # Если это корутина, нужно await (но мы уже в async функции)
                                                if hasattr(file_content_obj, '__await__'):
                                                    # Это корутина, нужно await
                                                    content_bytes = await file_content_obj.read()
                                                elif hasattr(file_content_obj, 'read'):
                                                    # Проверяем, не корутина ли это
                                                    read_result = file_content_obj.read()
                                                    if hasattr(read_result, '__await__'):
                                                        content_bytes = await read_result
                                                    else:
                                                        content_bytes = read_result
                                                else:
                                                    content_bytes = file_content_obj
                                                
                                                # Создаем BytesIO
                                                file_content_obj = BytesIO(content_bytes)
                                                file_content_obj.seek(0)
                                                print(f"✅ file_content_obj преобразован в BytesIO: {len(content_bytes)} байт")
                                            except Exception as convert_err:
                                                print(f"⚠️ Ошибка преобразования в BytesIO: {convert_err}")
                                                import traceback
                                                print(f"📋 Детали: {traceback.format_exc()}")
                                        
                                        # Отладка: проверяем размер файла
                                        file_size = 0
                                        try:
                                            if hasattr(file_content_obj, 'seek'):
                                                file_content_obj.seek(0)
                                                content_bytes = file_content_obj.read()
                                                file_size = len(content_bytes)
                                                file_content_obj.seek(0)  # Сбрасываем позицию для будущего чтения
                                                print(f"📊 Размер файла: {file_size} байт ({file_size / (1024*1024):.2f} MB)")
                                        except Exception as size_err:
                                            print(f"⚠️ Ошибка чтения размера: {size_err}")
                                        
                                        # Создаем объект с данными файла для передачи в process_local_file
                                        # Передаем BytesIO объект
                                        class FileData:
                                            def __init__(self, content_obj, name):
                                                self.content = content_obj  # BytesIO объект
                                                self.name = name
                                        
                                        # КРИТИЧНО: Сохраняем в ГЛОБАЛЬНУЮ переменную для надежности
                                        _global_uploaded_file_data = FileData(file_content_obj, file_name)
                                        
                                        # Также сохраняем в локальную переменную (для совместимости)
                                        uploaded_file_data = _global_uploaded_file_data
                                        
                                        print(f"✅ uploaded_file_data сохранен (глобально и локально): {uploaded_file_data is not None}, name = {uploaded_file_data.name}")
                                        
                                        # Логируем через smart_log (если доступен) и print
                                        log_msg = f"📎 Файл загружен: {file_name} (размер: {file_size} байт)"
                                        print(log_msg)
                                        try:
                                            smart_log(log_msg)
                                            smart_log(f"🔍 Отладка handle_upload: uploaded_file_data сохранен = {uploaded_file_data is not None}")
                                            smart_log(f"🔍 Отладка handle_upload: uploaded_file_data.name = {uploaded_file_data.name}")
                                        except:
                                            pass
                                        
                                        local_file_info.text = f'✅ Файл выбран: {file_name}'
                                        local_file_info.classes('mt-4 text-green-600')
                                        
                                        print(f"✅ handle_upload завершен успешно")
                                        
                                    except Exception as ex:
                                        import traceback
                                        error_details = traceback.format_exc()
                                        error_msg = f"❌ Ошибка в handle_upload: {str(ex)}"
                                        print(error_msg)
                                        print(f"📋 Детали: {error_details}")
                                        try:
                                            smart_log(error_msg)
                                            smart_log(f"📋 Детали: {error_details}")
                                        except:
                                            pass
                                        local_file_info.text = f'⚠️ Ошибка: {str(ex)}'
                                        local_file_info.classes('text-[#FF6B35]')
                            
                            # Создаем file_upload (скрытый, активируется через клик на dropzone)
                            file_upload = ui.upload(
                                on_upload=handle_upload,
                                max_file_size=10_000_000_000,  # 10 GB
                                auto_upload=True,
                                multiple=False
                            ).classes('hidden')
                            
                            # Переключатель источника (скрыт, так как в макете нет переключателя, но нужен для логики)
                            video_source = ui.radio(
                                ['YouTube', 'Локальный файл'],
                                value='YouTube'
                            ).classes('hidden')
                            
                            # Показываем/скрываем контейнеры в зависимости от выбора
                            def update_source_display():
                                if video_source and video_source.value == 'YouTube':
                                    youtube_container.set_visibility(True)
                                    local_file_container.set_visibility(False)
                                else:
                                    youtube_container.set_visibility(False)
                                    local_file_container.set_visibility(True)
                            
                            if video_source:
                                video_source.on('update:model-value', lambda: update_source_display())
                            update_source_display()  # Инициализация
                            
                            # Обновляем текст кнопки в зависимости от источника
                            download_button = None
                
                # ПАНЕЛЬ 2: ADDITIONAL OPTIONS
                with ui.card().classes('main-panel'):
                    # Заголовок панели
                    with ui.row().classes('panel-header'):
                        ui.label('[CONFIG:OPTIONS]').classes('panel-label')
                        ui.label('ADDITIONAL OPTIONS').classes('panel-title')
                        ui.icon('tune', size='20px', color='#FFD600')
                    
                    # Содержимое панели
                    with ui.column().classes('panel-content gap-5'):
                        
                        # СЕКЦИЯ 1: PROCESSING
                        with ui.column().classes('w-full gap-4'):
                            # Заголовок секции
                            with ui.row().classes('w-full items-center justify-between'):
                                with ui.row().classes('items-center gap-3'):
                                    ui.icon('settings', size='16px', color='#FFD600')
                                    ui.label('PROCESSING').classes('section-title')
                                ui.label('▼').classes('text-[#6B6B6B] text-[10px]')
                            
                            # Содержимое секции
                            with ui.column().classes('w-full gap-4'):
                                with ui.row().classes('w-full gap-4'):
                                    # LANGUAGE
                                    with ui.column().classes('gap-1.5 w-[160px]'):
                                        ui.label('LANGUAGE').classes('text-[#4D4D4D] text-[10px] font-bold').style('font-family: "Space Grotesk", sans-serif; letter-spacing: 1px;')
                                        language_select = ui.select(
                                            {'ru': 'Русский', 'en': 'Английский', None: 'AUTO'},
                                            value=None,
                                            label=''
                                        ).classes('w-full').style('background: #1A1A1A; border: 2px solid #2A2A2A; color: #F5F5F0; font-family: "Space Grotesk", sans-serif; font-size: 11px; font-weight: 700; height: 44px; padding: 0 12px;')
                                    
                                    # MODEL
                                    with ui.column().classes('gap-1.5 w-[160px]'):
                                        ui.label('MODEL').classes('text-[#4D4D4D] text-[10px] font-bold').style('font-family: "Space Grotesk", sans-serif; letter-spacing: 1px;')
                                        model_size_select = ui.select(
                                            {'tiny': 'Tiny', 'base': 'Base', 'small': 'Small', 'medium': 'Medium', 'large-v3': 'LARGE'},
                                            value='large-v3',
                                            label=''
                                        ).classes('w-full').style('background: #1A1A1A; border: 2px solid #2A2A2A; color: #F5F5F0; font-family: "Space Grotesk", sans-serif; font-size: 11px; font-weight: 700; height: 44px; padding: 0 12px;')
                                    
                                    # SPEAKERS
                                    with ui.column().classes('gap-1.5 w-[140px]'):
                                        ui.label('SPEAKERS').classes('text-[#4D4D4D] text-[10px] font-bold').style('font-family: "Space Grotesk", sans-serif; letter-spacing: 1px;')
                                        speaker_options = {None: 'AUTO'}
                                        for i in range(1, 6):
                                            speaker_options[i] = str(i)
                                        num_speakers_input = ui.select(
                                            options=speaker_options,
                                            value=None,
                                            label=''
                                        ).classes('w-full').style('background: #1A1A1A; border: 2px solid #2A2A2A; color: #F5F5F0; font-family: "Space Grotesk", sans-serif; font-size: 11px; font-weight: 700; height: 44px; padding: 0 12px;')
                                
                            # Чекбоксы
                            with ui.column().classes('w-full gap-2 mt-2'):
                                with ui.row().classes('items-center gap-2.5'):
                                    diarize_checkbox = ui.checkbox('Diarization', value=True).classes('custom-checkbox')
                                    transcribe_checkbox = ui.checkbox('Transcribe', value=True).classes('custom-checkbox')
                                
                                with ui.row().classes('items-center gap-2.5'):
                                    correct_speakers_checkbox = ui.checkbox('Clone voice', value=False).classes('custom-checkbox')
                            
                            # Hugging Face Token (скрыто, но нужно для функциональности)
                            hf_token_input = ui.input(
                                label='Hugging Face Token',
                                placeholder='hf_... (опционально)',
                                password=True
                            ).classes('hidden')
                            
                            # Разделитель
                            ui.element('div').classes('w-full h-[2px] bg-[#2A2A2A]')
                        
                        # СЕКЦИЯ 2: TRANSLATION
                        with ui.column().classes('w-full gap-4'):
                            # Заголовок секции
                            with ui.row().classes('w-full items-center justify-between'):
                                with ui.row().classes('items-center gap-3'):
                                    ui.icon('translate', size='16px', color='#FFD600')
                                    ui.label('TRANSLATION').classes('section-title')
                                ui.label('▼').classes('text-[#6B6B6B] text-[10px]')
                            
                            # Содержимое секции
                                with ui.row().classes('w-full gap-4 items-center'):
                                    translate_checkbox = ui.checkbox('Enable', value=False)
                                    
                                    # TARGET
                                    with ui.column().classes('gap-1.5 w-[160px]'):
                                        ui.label('TARGET').classes('text-[#4D4D4D] text-[10px] font-bold').style('font-family: "Space Grotesk", sans-serif; letter-spacing: 1px;')
                                        translate_target_lang_select = ui.select(
                                            {'ru': 'RUSSIAN', 'en': 'ENGLISH', 'es': 'SPANISH', 'fr': 'FRENCH', 'de': 'GERMAN'},
                                            value='ru',
                                            label=''
                                        ).classes('w-full').style('background: #1A1A1A; border: 2px solid #2A2A2A; color: #F5F5F0; font-family: "Space Grotesk", sans-serif; font-size: 11px; font-weight: 700; height: 44px; padding: 0 12px;')
                                    
                                    # PROVIDER
                                    with ui.column().classes('gap-1.5 w-[180px]'):
                                        ui.label('PROVIDER').classes('text-[#4D4D4D] text-[10px] font-bold').style('font-family: "Space Grotesk", sans-serif; letter-spacing: 1px;')
                                        translate_provider_select = ui.select(
                                            {'api': 'QUALITY API', 'ollama': 'Ollama (LLM)'},
                                            value='api',
                                            label=''
                                        ).classes('w-full').style('background: #1A1A1A; border: 2px solid #2A2A2A; color: #F5F5F0; font-family: "Space Grotesk", sans-serif; font-size: 11px; font-weight: 700; height: 44px; padding: 0 12px;')
                            
                            # Разделитель
                            ui.element('div').classes('w-full h-[2px] bg-[#2A2A2A]')
                        
                        # СЕКЦИЯ 3: VOICE CLONING
                        with ui.column().classes('w-full gap-4'):
                            # Заголовок секции
                            with ui.row().classes('w-full items-center justify-between'):
                                with ui.row().classes('items-center gap-3'):
                                    ui.icon('record_voice_over', size='16px', color='#FF6B35')
                                    ui.label('VOICE CLONING').classes('section-title')
                                ui.label('▼').classes('text-[#6B6B6B] text-[10px]')
                            
                            # Содержимое секции
                                with ui.row().classes('w-full gap-4 items-center'):
                                    voice_cloning_checkbox = ui.checkbox('Enable dubbing', value=False)
                                    
                                    # Подсказка
                                    with ui.row().classes('items-center gap-1.5 bg-[#1A1A1A] border-l-[3px] border-[#FF6B35] px-2.5 py-1.5'):
                                        ui.label('[!]').classes('text-[#FF6B35] text-[9px] font-bold').style('font-family: "IBM Plex Mono", monospace;')
                                        ui.label('Python 3.10+ required').classes('text-[#F5F5F0] text-[10px]').style('font-family: "IBM Plex Mono", monospace;')
                                
                                # Кнопка для ручного запуска voice cloning
                                async def start_voice_cloning_manual():
                                    if not segments_path or not os.path.exists(segments_path):
                                        ui.notify('Ошибка: Сначала выполните транскрипцию и перевод!', color='negative')
                                        return
                                    
                                    target_lang = translate_target_lang_select.value if translate_target_lang_select else "ru"
                                    translated_path = segments_path.replace('_segments.json', f'_translated_{target_lang}_segments.json')
                                    
                                    if not os.path.exists(translated_path):
                                        ui.notify('Ошибка: Сначала выполните перевод!', color='negative')
                                        return
                                    
                                    await start_voice_cloning(translated_path, target_lang)
                
                # КНОПКА START PROCESSING
                download_button = ui.button('START PROCESSING', on_click=start_processing) \
                    .classes('w-full h-16 bg-[#FFD600] text-[#1A1A1A] text-base font-bold flex items-center justify-center gap-4') \
                    .style('font-family: "Space Grotesk", sans-serif; letter-spacing: 2px;')
                
                # Обновляем текст кнопки при смене источника
                def update_button_text():
                    if download_button:
                        download_button.text = 'START PROCESSING'
                
                if video_source:
                    video_source.on('update:model-value', lambda: (update_source_display(), update_button_text()))

        # === НИЖНЯЯ ЧАСТЬ (ТЕРМИНАЛ ПО МАКЕТУ) ===
        with splitter.after:
            with ui.element('div').classes('absolute inset-0 flex flex-col bg-[#0A0A0A] border-t-[3px] border-[#2A2A2A] overflow-hidden'):
                
                # Шапка терминала
                with ui.row().classes('w-full bg-[#0F0F0F] h-9 items-center justify-between px-4 border-b-[2px] border-[#2A2A2A]'):
                    with ui.row().classes('items-center gap-3'):
                        ui.icon('terminal', size='14px', color='#FFD600')
                        ui.label('[TERMINAL:OUTPUT]').classes('text-[#FFD600] text-[10px] font-bold').style('font-family: "IBM Plex Mono", monospace; letter-spacing: 1px;')
                    
                    with ui.row().classes('items-center gap-1 px-2 py-1 border-2 border-[#3D3D3D] cursor-pointer hover:border-[#6B6B6B] transition-colors') \
                        .on('click', clear_log):
                        ui.icon('delete', size='12px', color='#6B6B6B')
                        ui.label('CLEAR').classes('text-[#6B6B6B] text-[9px] font-bold').style('font-family: "Space Grotesk", sans-serif; letter-spacing: 1px;')

                # Лог
                log_view = ui.log().classes('flex-1 min-h-0 w-full bg-[#0A0A0A] text-[#FFD600] p-4 overflow-auto whitespace-pre-wrap leading-tight') \
                    .style('font-family: "IBM Plex Mono", monospace; font-size: 11px; user-select: text !important; -webkit-user-select: text !important; -moz-user-select: text !important; -ms-user-select: text !important;')
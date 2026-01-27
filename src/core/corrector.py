# -*- coding: utf-8 -*-
import json
import requests
import logging
import re
from typing import List, Dict, Optional, Callable, Any

# Логирование
logger = logging.getLogger(__name__)

class SpeakerCorrector:
    """
    Класс для исправления ошибок присвоения спикеров с помощью LLM
    и умного объединения сегментов с ограничением длины.
    """
    
    def __init__(
        self,
        ollama_url: str = "http://localhost:11434",
        model: str = "qwen2.5:7b",
        progress_callback: Optional[Callable[[str], None]] = None
    ):
        self.ollama_url = ollama_url.rstrip('/')
        self.model = model
        self.progress_callback = progress_callback or (lambda msg: None)
    
    def _log(self, message: str):
        """Внутренний метод для логирования"""
        self.progress_callback(message)
        logger.info(message)
    
    def correct(self, segments: List[Dict]) -> List[Dict]:
        """
        Основной метод коррекции.
        """
        if not segments:
            self._log("⚠️ Пустой список сегментов - коррекция пропущена")
            return segments
        
        self._log(f"🔧 Начало коррекции спикеров: {len(segments)} сегментов")
        
        # ОТЛАДКА: Показываем исходных спикеров
        original_speakers = {}
        for i, seg in enumerate(segments):
            speaker = seg.get("speaker", "SPEAKER_UNKNOWN")
            if speaker not in original_speakers:
                original_speakers[speaker] = []
            original_speakers[speaker].append(i)
        
        self._log(f"📊 Исходное распределение спикеров:")
        for speaker, indices in original_speakers.items():
            self._log(f"   {speaker}: {len(indices)} сегментов (ID: {indices[:5]}{'...' if len(indices) > 5 else ''})")
        
        # ШАГ A: Подготовка (ID Mapping)
        segments_with_id = self._prepare_segments(segments)
        
        # ШАГ B: LLM Коррекция
        correction_map = self._llm_correct(segments_with_id)
        
        # ШАГ C: Применение исправлений
        corrected_segments = self._apply_corrections(segments_with_id, correction_map)
        
        # ШАГ D: Умное объединение (с лимитом длины)
        merged_segments = self._smart_merge(corrected_segments)
        
        # ОТЛАДКА: Показываем финальное распределение спикеров
        final_speakers = {}
        for i, seg in enumerate(merged_segments):
            speaker = seg.get("speaker", "SPEAKER_UNKNOWN")
            if speaker not in final_speakers:
                final_speakers[speaker] = []
            final_speakers[speaker].append(i)
        
        self._log(f"📊 Финальное распределение спикеров:")
        for speaker, indices in final_speakers.items():
            self._log(f"   {speaker}: {len(indices)} сегментов")
        
        self._log(f"✅ Коррекция завершена: {len(merged_segments)} сегментов (было {len(segments)})")
        
        return merged_segments
    
    def _prepare_segments(self, segments: List[Dict]) -> List[Dict]:
        """Присваивает временный _id и приводит типы к float"""
        prepared = []
        for idx, seg in enumerate(segments):
            seg_copy = seg.copy()
            seg_copy['_id'] = idx
            if "start" in seg_copy: seg_copy["start"] = float(seg_copy["start"])
            if "end" in seg_copy: seg_copy["end"] = float(seg_copy["end"])
            prepared.append(seg_copy)
        return prepared
    
    def _llm_correct(self, segments_with_id: List[Dict]) -> Dict[int, str]:
        """Отправляет сегменты в LLM и получает маппинг исправлений"""
        
        # Упрощаем данные для экономии токенов, но добавляем контекст (предыдущий и следующий сегмент)
        simplified = []
        for idx, seg in enumerate(segments_with_id):
            seg_data = {
                "id": seg["_id"],  # LLM лучше понимает "id" чем "_id"
                "text": seg.get("text", ""),
                "speaker": seg.get("speaker", "SPEAKER_UNKNOWN")
            }
            # Добавляем контекст для лучшего понимания грамматических продолжений
            if idx > 0:
                prev_text = segments_with_id[idx - 1].get("text", "")
                seg_data["prev_text"] = prev_text[-50:] if len(prev_text) > 50 else prev_text  # Последние 50 символов предыдущего
            if idx < len(segments_with_id) - 1:
                next_text = segments_with_id[idx + 1].get("text", "")
                seg_data["next_text"] = next_text[:50] if len(next_text) > 50 else next_text  # Первые 50 символов следующего
            simplified.append(seg_data)
        
        system_prompt = (
            "You are an Expert Dialogue Diarization AI. Your goal is to reconstruct the perfect conversation flow by assigning the correct speaker to each text segment based on DEEP SEMANTIC ANALYSIS.\n\n"
            
            "### CORE MISSION\n"
            "The input is a list of 'chopped' audio segments. They may be split mid-sentence or contain rapid exchanges. "
            "You must identify WHO is speaking by analyzing the context, logic, and grammar of the dialogue.\n\n"
            
            "### CRITICAL RULE #1: GRAMMATICAL CONTINUITY (HIGHEST PRIORITY)\n"
            "If a sentence is split across multiple segments, they MUST have the SAME speaker. This is NON-NEGOTIABLE.\n\n"
            "**How to detect grammatical continuity:**\n"
            "- If Segment N ends with a COMMA [,] and Segment N+1 starts with a lowercase letter, preposition, conjunction, or continues the thought grammatically, "
            "  they are ONE sentence and MUST have the SAME speaker.\n"
            "- If Segment N ends with an INCOMPLETE CONJUNCTION/PREPOSITION (e.g., 'А еще', 'И еще', 'Но', 'А', 'И', 'В', 'На') "
            "  without punctuation, and Segment N+1 starts with a lowercase letter, they are ONE sentence and MUST have the SAME speaker.\n"
            "- Example 1: 'говорят нас, люди бедные' (ends with comma) + 'хлеб без соли доедают' (starts lowercase) = ONE sentence = ONE speaker.\n"
            "- Example 2: 'А еще' (ends with conjunction) + 'говорят нас, люди бедные' (starts lowercase) = ONE sentence = ONE speaker.\n"
            "- The speaker of the FIRST part (ending with comma/conjunction) is the CORRECT speaker for BOTH parts.\n"
            "- If you see this pattern, assign the SAME speaker to both segments, using the speaker from the segment that ENDS with comma/conjunction.\n"
            "- IMPORTANT: Even if there is a large time gap between segments, if they are grammatically connected, they MUST have the SAME speaker.\n\n"
            
            "### ANALYSIS RULES (Follow strictly):\n"
            "1. **Semantic Continuity - CRITICAL**: If a sentence is split across multiple segments, they MUST have the SAME speaker. "
            "   - If Segment N ends with a COMMA [,] and Segment N+1 continues grammatically (e.g., 'говорят нас, люди бедные' → 'хлеб без соли доедают'), "
            "     they are ONE sentence and MUST have the SAME speaker. This is NON-NEGOTIABLE.\n"
            "   - If Segment N ends with an INCOMPLETE CONJUNCTION/PREPOSITION without punctuation (e.g., 'А еще', 'И еще', 'Но', 'А', 'И', 'В', 'На'), "
            "     and Segment N+1 starts with a lowercase letter, they are ONE sentence and MUST have the SAME speaker.\n"
            "   - Example 1: 'говорят нас, люди бедные' (SPEAKER_01) + 'хлеб без соли доедают' (SPEAKER_00) → BOTH should be SPEAKER_01.\n"
            "   - Example 2: 'А еще' (SPEAKER_00) + 'говорят нас, люди бедные' (SPEAKER_01) → BOTH should be SPEAKER_00 (the one who started with 'А еще').\n"
            "   - Look for grammatical incompleteness: segment ends with adjective/comma/conjunction → next starts with noun/verb/lowercase = continuation = SAME speaker.\n"
            "   - If Segment N ends with a comma or incomplete conjunction and Segment N+1 starts with a lowercase letter, preposition, conjunction, or continues the thought grammatically, "
            "     it's DEFINITELY the SAME speaker. Do NOT assign different speakers to parts of one sentence.\n"
            "   - **IMPORTANT**: When you detect this pattern, use the speaker from the segment that ENDS with comma/conjunction as the correct speaker for BOTH segments.\n"
            "   - **CRITICAL**: Even if there is a large time gap between segments, if they are grammatically connected, they MUST have the SAME speaker.\n"
            "2. **Q&A Logic - CRITICAL**: If Segment N ends with a QUESTION [?] and Segment N+1 is an ANSWER (especially starting with a number, 'Yes', 'No', or a direct response), "
            "   they likely belong to DIFFERENT speakers. Example: 'How much do you make? 72 thousand.' → Question (Speaker A) + Answer (Speaker B).\n"
            "   - Exception: Rhetorical questions or self-corrections belong to the SAME speaker.\n"
            "   - IMPORTANT: Distinguish between comma (continuation) and question mark (potential speaker change).\n"
            "3. **Short Reactions**: Words like 'Yes', 'No', 'Exactly', 'What?', 'Really?' usually belong to the LISTENER (change of speaker), "
            "   UNLESS they are part of a continuous monologue (e.g., 'I went there. Yes, I did.').\n"
            "4. **Interruptions & Emotional Outbursts**: If Segment N ends with an interruption, emotional outburst, or sudden cutoff, "
            "   and Segment N+1 is a reaction or response, CHANGE the speaker. Example: 'Show me your ID!' → 'Yes, of course.' = different speakers.\n"
            "5. **Dialogue vs Monologue**: Detect if a speaker is telling a story. In a story, even if they quote someone else, the SPEAKER label usually remains the narrator, "
            "   UNLESS the context implies an interruption or response from another character.\n"
            "6. **Speaker Consistency**: Minimize unnecessary speaker switches. If it's ambiguous, assume the current speaker continues.\n"
            "7. **Number Responses**: If a segment starts with a number (e.g., '72 thousand', '22 million') and follows a question, "
            "   it is almost ALWAYS a response from a DIFFERENT speaker answering the question.\n\n"
            
            "### INPUT FORMAT\n"
            "Each segment may include 'prev_text' (previous segment ending) and 'next_text' (next segment beginning) for context.\n"
            "Use this context to detect grammatical continuity. If 'prev_text' ends with comma and current 'text' starts lowercase, "
            "they are ONE sentence = SAME speaker (use the speaker from the segment with comma).\n\n"
            
            "### OUTPUT FORMAT\n"
            "Return a strictly valid JSON LIST of objects with 'id' and 'speaker'. "
            "Example:\n"
            "[{\"id\": 0, \"speaker\": \"SPEAKER_00\"}, {\"id\": 1, \"speaker\": \"SPEAKER_01\"}, {\"id\": 2, \"speaker\": \"SPEAKER_00\"}]\n"
            "CRITICAL: The output list MUST contain exactly the same number of items as the input. Return ALL segments.\n"
            "CRITICAL: When you detect grammatical continuity (comma + lowercase continuation), assign the SAME speaker to both segments, using the speaker from the segment that ENDS with comma.\n"
            "CRITICAL: Analyze the 'prev_text' and 'next_text' fields to understand context and detect split sentences."
        )
        
        self._log(f"📤 Отправка {len(simplified)} сегментов в LLM")
        
        # ОТЛАДКА: Показываем первые несколько сегментов и ищем грамматические продолжения
        if len(simplified) > 0:
            preview = simplified[:5]
            self._log(f"📋 Пример входных данных (первые 5):")
            for seg in preview:
                text_preview = seg['text'][:60] + "..." if len(seg['text']) > 60 else seg['text']
                context_info = ""
                if 'prev_text' in seg:
                    context_info += f" [prev: ...{seg['prev_text'][-20:]}]"
                if 'next_text' in seg:
                    context_info += f" [next: {seg['next_text'][:20]}...]"
                self._log(f"   ID {seg['id']}: [{seg['speaker']}] {text_preview}{context_info}")
            
            # Ищем грамматические продолжения для отладки
            for i in range(len(simplified) - 1):
                current = simplified[i]
                next_seg = simplified[i + 1]
                current_text = current.get('text', '').strip()
                next_text = next_seg.get('text', '').strip()
                
                if current_text.endswith(',') and next_text and next_text[0].islower():
                    self._log(f"⚠️ Обнаружено грамматическое продолжение: ID {current['id']} (ends with ',') → ID {next_seg['id']} (starts lowercase)")
                    self._log(f"   Текущие спикеры: {current['speaker']} → {next_seg['speaker']}")
                    if current['speaker'] != next_seg['speaker']:
                        self._log(f"   ❌ РАЗНЫЕ спикеры! LLM должен исправить это!")
        
        try:
            response = requests.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model": self.model,
                    "system": system_prompt,
                    "prompt": json.dumps(simplified, ensure_ascii=False),
                    "stream": False,
                    "options": {"temperature": 0.1}
                },
                timeout=120
            )
            
            if response.status_code != 200:
                self._log(f"⚠️ Ошибка Ollama: {response.status_code}")
                return {}

            response_text = response.json().get("response", "").strip()
            
            # ОТЛАДКА: Показываем сырой ответ
            self._log(f"📥 Получен ответ от LLM (длина: {len(response_text)} символов)")
            if len(response_text) > 0:
                preview_response = response_text[:500] if len(response_text) > 500 else response_text
                self._log(f"📄 Начало ответа: {preview_response}...")
            
            # Чистка от Markdown
            if "```" in response_text:
                response_text = response_text.replace("```json", "").replace("```", "").strip()
            
            # Парсинг с улучшенной обработкой ошибок
            data = None
            try:
                data = json.loads(response_text)
                self._log(f"🔍 Распарсено: {type(data).__name__}")
            except json.JSONDecodeError as e:
                self._log(f"⚠️ Ошибка парсинга JSON: {e}")
                
                # Попытка 1: Исправить распространенные ошибки LLM
                fixed_text = response_text
                
                # Исправляем двойные кавычки в ключах: {"id": 1": 10} → {"id": 10}
                fixed_text = re.sub(r'"id":\s*(\d+)"\s*:\s*(\d+)', r'"id": \2', fixed_text)
                
                # Исправляем лишние кавычки: "id": "0" → "id": 0
                fixed_text = re.sub(r'"id":\s*"(\d+)"', r'"id": \1', fixed_text)
                
                # Исправляем отсутствие запятых между объектами
                fixed_text = re.sub(r'}\s*{', r'}, {', fixed_text)
                
                # Добавляем закрывающую скобку, если отсутствует
                if not fixed_text.rstrip().endswith("]"):
                    # Проверяем, что это список
                    if fixed_text.strip().startswith("["):
                        fixed_text = fixed_text.rstrip() + "]"
                
                try:
                    data = json.loads(fixed_text)
                    self._log(f"✅ JSON исправлен и распарсен")
                except Exception as e2:
                    self._log(f"⚠️ Первая попытка исправления не удалась: {e2}")
                    
                    # Попытка 2: Извлечь все объекты с помощью regex
                    try:
                        # Ищем все объекты вида {"id": X, "speaker": "Y"}
                        pattern = r'\{"id"\s*:\s*(\d+)\s*,\s*"speaker"\s*:\s*"([^"]+)"\}'
                        matches = re.findall(pattern, fixed_text)
                        
                        if matches:
                            data = [{"id": int(m[0]), "speaker": m[1]} for m in matches]
                            self._log(f"✅ JSON извлечен через regex: найдено {len(data)} сегментов")
                        else:
                            raise ValueError("Не найдено совпадений")
                    except Exception as e3:
                        self._log(f"❌ Не удалось распарсить ответ LLM после всех попыток: {e3}")
                        self._log(f"📄 Проблемный текст (первые 500 символов): {response_text[:500]}...")
                        return {}

            # Преобразование в map {id: speaker}
            result_map = {}
            if isinstance(data, list):
                for item in data:
                    if "id" in item and "speaker" in item:
                        result_map[item["id"]] = item["speaker"]
            
            # ОТЛАДКА: Показываем детальную информацию
            self._log(f"📥 LLM вернул {len(result_map)}/{len(simplified)} сегментов")
            
            if len(result_map) < len(simplified):
                missing = len(simplified) - len(result_map)
                self._log(f"⚠️ LLM не вернул {missing} сегментов - они останутся без изменений")
            
            # Показываем примеры исправлений
            if result_map:
                changes_examples = []
                for seg_id, new_speaker in list(result_map.items())[:5]:  # Первые 5
                    if seg_id < len(segments_with_id):
                        old_speaker = segments_with_id[seg_id].get("speaker", "UNKNOWN")
                        if old_speaker != new_speaker:
                            changes_examples.append(f"ID {seg_id}: {old_speaker} → {new_speaker}")
                
                if changes_examples:
                    self._log(f"📋 Примеры изменений: {', '.join(changes_examples)}")
                else:
                    self._log(f"ℹ️ LLM подтвердил текущих спикеров (изменений нет)")
            
            return result_map

        except Exception as e:
            self._log(f"❌ Сбой запроса к LLM: {e}")
            return {}

    def _apply_corrections(self, segments: List[Dict], correction_map: Dict[int, str]) -> List[Dict]:
        """Применяет исправления, не трогая тайминги"""
        corrected = []
        changes = 0
        applied = 0
        not_found = 0
        
        for seg in segments:
            seg_copy = seg.copy()
            seg_id = seg_copy.get("_id")
            old_speaker = seg_copy.get("speaker", "SPEAKER_UNKNOWN")
            
            if seg_id in correction_map:
                new_speaker = correction_map[seg_id]
                applied += 1
                if new_speaker != old_speaker:
                    seg_copy["speaker"] = new_speaker
                    changes += 1
                    self._log(f"🔄 Сегмент {seg_id}: {old_speaker} → {new_speaker}")
            else:
                not_found += 1
                # LLM не вернул этот сегмент - оставляем оригинальный спикер
            
            if "_id" in seg_copy: del seg_copy["_id"] # Чистим мусор
            corrected.append(seg_copy)
        
        # Детальная статистика
        self._log(f"📊 Статистика применения: применено {applied}/{len(segments)}, изменено {changes}, не найдено {not_found}")
        
        if changes == 0 and applied > 0:
            self._log(f"ℹ️ LLM подтвердил всех спикеров (изменений не требуется)")
        elif changes == 0 and applied == 0:
            self._log(f"⚠️ LLM не вернул ни одного сегмента - исправления не применены")
        
        return corrected

    def _smart_merge(self, segments: List[Dict]) -> List[Dict]:
        """
        Умная склейка: объединяет соседние сегменты одного спикера,
        исправляет SPEAKER_UNKNOWN, устраняет перекрытия таймингов.
        """
        if not segments: return []

        # ШАГ 1: Исправляем SPEAKER_UNKNOWN (заменяем на ближайшего спикера)
        segments = self._fix_unknown_speakers(segments)
        
        # ШАГ 2: Исправляем перекрывающиеся тайминги
        segments = self._fix_overlapping_timestamps(segments)

        merged = []
        current = segments[0].copy()
        
        # Лимиты
        MAX_MERGE_DURATION = 8.0  # Увеличено до 8 сек для более естественных фраз
        MAX_GAP = 1.5             # Увеличено до 1.5 сек для склейки связанных фраз

        for next_seg in segments[1:]:
            current_start = float(current['start'])
            current_end = float(current['end'])
            next_start = float(next_seg['start'])
            next_end = float(next_seg['end'])
            
            # Вычисляем параметры потенциальной склейки
            potential_duration = next_end - current_start
            gap = next_start - current_end
            
            # Проверяем грамматическую связность
            current_text = current.get('text', '').strip()
            next_text = next_seg.get('text', '').strip()
            
            # КРИТИЧНО: Если текущий сегмент заканчивается на запятую, а следующий продолжает грамматически
            # - это ОДНО предложение, разбитое посередине = ОДИН спикер
            ends_with_comma = current_text.endswith(',') or current_text.endswith('，')  # Запятая или китайская запятая
            
            # НОВОЕ: Проверяем незавершенные предложения (сегмент заканчивается на союз/предлог без пунктуации)
            # НО: Будем осторожны - "А еще" может быть началом новой мысли другого спикера
            current_text_lower = current_text.lower().strip()
            
            # КРИТИЧНО: Проверяем, есть ли перед "А еще" / "И еще" точка или другой знак завершения предложения
            # Если есть - это НЕ продолжение, а новая мысль
            has_sentence_end_before = bool(re.search(
                r'[.!?]\s+(а\s+еще|и\s+еще|но\s+при|а\s+вот|и\s+вот)\s*$',
                current_text_lower
            ))
            
            # Проверка 1: Заканчивается на союз/предлог без пунктуации (но не после точки)
            ends_with_single_conjunction = bool(re.search(
                r'\s+(а|и|но|да|в|на|с|к|от|до|за|по|под|над|при|про|без|для|из|о|об|у|со|во|что|который|где|когда)\s*$',
                current_text_lower
            )) and not has_sentence_end_before
            
            # Проверка 2: Заканчивается на фразы типа "А еще", "И еще" (но не после точки)
            ends_with_phrase = bool(re.search(
                r'(а\s+еще|и\s+еще|но\s+при|а\s+вот|и\s+вот|а\s+вот\s+еще|и\s+вот\s+еще|а\s+потом|и\s+потом|а\s+теперь|и\s+теперь)\s*$',
                current_text_lower
            )) and not has_sentence_end_before
            
            # Проверка 3: Заканчивается на "еще" (но не после точки)
            ends_with_еще = (current_text_lower.endswith('еще') or current_text_lower.endswith('ещё')) and not has_sentence_end_before
            
            ends_with_incomplete = ends_with_single_conjunction or ends_with_phrase or ends_with_еще
            
            # ОТЛАДКА: Если обнаружили "А еще" после точки - это НЕ продолжение
            if has_sentence_end_before:
                self._log(f"⚠️ Обнаружено 'А еще' после точки - это НЕ продолжение, а новая мысль: '{current_text[-50:]}'")
            
            # Проверяем грамматическое продолжение
            if next_text and len(next_text) > 0:
                next_first_char = next_text[0]
                next_starts_lowercase = next_first_char.islower()  # С маленькой буквы
                # Проверяем русские буквы (маленькие)
                next_starts_russian_lowercase = bool(re.search(r'^[а-яё]', next_text))
                # Проверяем союзы/местоимения/предлоги
                next_starts_with_connector = bool(re.search(
                    r'^(и|а|но|да|нет|что|который|где|когда|в|на|с|к|от|до|за|по|под|над|при|про|без|для|из|о|об|у|со|во)',
                    next_text.lower()
                ))
            else:
                next_starts_lowercase = False
                next_starts_russian_lowercase = False
                next_starts_with_connector = False
            
            # Грамматическое продолжение: 
            # 1. Запятая + продолжение (маленькая буква или союз/местоимение)
            # 2. НЕЗАВЕРШЕННОЕ предложение (союз/предлог без пунктуации) + продолжение
            # НО: "А еще" после точки - это НЕ продолжение, а новая мысль
            next_continues_grammatically = (
                (ends_with_comma and (
                    next_starts_lowercase or 
                    next_starts_russian_lowercase or
                    next_starts_with_connector
                )) or
                (ends_with_incomplete and not has_sentence_end_before and (
                    next_starts_lowercase or 
                    next_starts_russian_lowercase
                ))
            )
            
            # ОТЛАДКА: Логируем обнаружение незавершенных предложений
            if ends_with_incomplete and not ends_with_comma:
                self._log(f"🔍 Обнаружено незавершенное предложение: '{current_text[-50:]}'")
                self._log(f"   → Следующий сегмент начинается: '{next_text[:30]}...' (lowercase: {next_starts_lowercase}, russian: {next_starts_russian_lowercase})")
                self._log(f"   → Грамматическое продолжение: {next_continues_grammatically}, gap: {gap:.1f}s")
            
            # УСЛОВИЯ ОБЪЕДИНЕНИЯ:
            # 1. Тот же спикер ИЛИ грамматическое продолжение (запятая + продолжение = один спикер)
            # 2. Маленькая пауза или перекрытие (НО для грамматического продолжения игнорируем большой gap)
            # 3. Не превышаем лимит длины
            same_speaker = current['speaker'] == next_seg['speaker']
            is_grammatical_continuation = next_continues_grammatically  # Запятая + продолжение = один спикер
            
            # ВАЖНО: Для грамматически связанных сегментов игнорируем большой gap
            # НО: Для незавершенных предложений (особенно "А еще" после точки) будем осторожнее
            if is_grammatical_continuation:
                if ends_with_incomplete:
                    # Незавершенное предложение - объединяем, но с ограничением gap (10 сек)
                    # Если "А еще" после точки, то ends_with_incomplete уже будет False, так что здесь только настоящие незавершенные
                    max_gap_for_incomplete = 10.0  # Более строгий лимит для незавершенных предложений
                    gap_acceptable = gap < max_gap_for_incomplete
                    self._log(f"   ℹ️ Незавершенное предложение - объединяем при gap < {max_gap_for_incomplete}s (текущий: {gap:.1f}s)")
                else:
                    # Запятая + продолжение - объединяем даже при большом gap (30 сек)
                    small_gap = True  # Игнорируем gap для грамматически связанных
                    max_gap_for_grammatical = 30.0  # Максимальный gap для грамматически связанных сегментов
                    gap_acceptable = gap < max_gap_for_grammatical
            else:
                small_gap = gap < MAX_GAP or gap < 0  # Разрешаем небольшое перекрытие
                gap_acceptable = True
            
            within_limit = potential_duration < MAX_MERGE_DURATION
            
            # Объединяем если: тот же спикер ИЛИ грамматическое продолжение (запятая/незавершенное)
            if (same_speaker or is_grammatical_continuation) and gap_acceptable and within_limit:
                # Если это грамматическое продолжение, но спикеры разные - исправляем спикера
                if is_grammatical_continuation and not same_speaker:
                    # Это одно предложение, разбитое посередине - должен быть один спикер
                    # ВАЖНО: Используем спикера сегмента, который заканчивается на запятую/союз (начало предложения)
                    old_speaker_next = next_seg['speaker']
                    old_speaker_current = current['speaker']
                    correct_speaker = current['speaker']  # Сегмент с запятой/союзом = начало предложения
                    
                    # ИСПРАВЛЯЕМ: Присваиваем правильного спикера ОБОИМ сегментам ДО объединения
                    current['speaker'] = correct_speaker
                    next_seg['speaker'] = correct_speaker
                    
                    gap_info = f" (gap: {gap:.1f}s)" if gap > 0 else ""
                    self._log(f"🔧 Исправлено грамматическое продолжение{gap_info}: '{current_text[-40:]}' + '{next_text[:40]}'")
                    self._log(f"   📝 Начало предложения [{old_speaker_current}] → [{correct_speaker}]")
                    self._log(f"   📝 Продолжение [{old_speaker_next}] → [{correct_speaker}]")
                
                # Клеим! (теперь current уже имеет правильного спикера)
                current['end'] = max(current_end, next_end)  # Берем максимальный end
                current['text'] = (current_text + " " + next_text).strip()
                if 'words' in current and 'words' in next_seg:
                    current['words'].extend(next_seg['words'])
                self._log(f"   ✅ Объединено в один сегмент: [{current['speaker']}] '{current['text'][:60]}...'")
            else:
                # Сохраняем и начинаем новый
                merged.append(current)
                current = next_seg.copy()

        merged.append(current)
        
        if len(merged) < len(segments):
            self._log(f"🔗 Склеено: {len(segments)} → {len(merged)} сегментов")
        
        return merged
    
    def _fix_unknown_speakers(self, segments: List[Dict]) -> List[Dict]:
        """Исправляет SPEAKER_UNKNOWN, заменяя на ближайшего спикера"""
        if not segments:
            return segments
        
        fixed = []
        known_speakers = set()
        
        # Собираем всех известных спикеров
        for seg in segments:
            speaker = seg.get('speaker', 'SPEAKER_UNKNOWN')
            if speaker != 'SPEAKER_UNKNOWN':
                known_speakers.add(speaker)
        
        if not known_speakers:
            # Если нет известных спикеров, используем SPEAKER_00 по умолчанию
            known_speakers.add('SPEAKER_00')
        
        # Исправляем SPEAKER_UNKNOWN
        for i, seg in enumerate(segments):
            seg_copy = seg.copy()
            speaker = seg_copy.get('speaker', 'SPEAKER_UNKNOWN')
            
            if speaker == 'SPEAKER_UNKNOWN':
                # Ищем ближайшего спикера (смотрим предыдущий и следующий)
                replacement = None
                
                # Смотрим предыдущий
                if i > 0:
                    prev_speaker = segments[i-1].get('speaker', 'SPEAKER_UNKNOWN')
                    if prev_speaker != 'SPEAKER_UNKNOWN':
                        replacement = prev_speaker
                
                # Если не нашли, смотрим следующий
                if not replacement and i < len(segments) - 1:
                    next_speaker = segments[i+1].get('speaker', 'SPEAKER_UNKNOWN')
                    if next_speaker != 'SPEAKER_UNKNOWN':
                        replacement = next_speaker
                
                # Если все еще не нашли, берем первого известного
                if not replacement:
                    replacement = sorted(known_speakers)[0]
                
                seg_copy['speaker'] = replacement
                self._log(f"🔧 Исправлен SPEAKER_UNKNOWN → {replacement}")
            
            fixed.append(seg_copy)
        
        return fixed
    
    def _fix_overlapping_timestamps(self, segments: List[Dict]) -> List[Dict]:
        """Исправляет перекрывающиеся тайминги"""
        if not segments:
            return segments
        
        fixed = []
        for i, seg in enumerate(segments):
            seg_copy = seg.copy()
            start = float(seg_copy.get('start', 0))
            end = float(seg_copy.get('end', 0))
            
            # Если есть предыдущий сегмент, проверяем перекрытие
            if i > 0:
                prev_end = float(fixed[i-1].get('end', 0))
                if start < prev_end:
                    # Есть перекрытие - сдвигаем start на prev_end
                    seg_copy['start'] = prev_end
                    self._log(f"🔧 Исправлено перекрытие: {start} → {prev_end}")
            
            # Убеждаемся, что end >= start
            if end < seg_copy['start']:
                seg_copy['end'] = seg_copy['start'] + 0.1  # Минимальная длительность
            
            fixed.append(seg_copy)
        
        return fixed
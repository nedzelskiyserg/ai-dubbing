# -*- coding: utf-8 -*-
"""
Elastic Timing — интеллектуальное управление таймингами для дубляжа.

Профессиональный дублёр не привязан жёстко к таймингу каждой фразы —
он использует паузы между фразами, чтобы естественно закончить мысль.

Два режима:
  1. compute_effective_durations() — базовое расширение в паузы (для перевода)
  2. compute_adaptive_durations() — адаптивное расширение с учётом
     фактических длительностей TTS (для VideoMaker)

Ключевые принципы:
  - Сегмент может расширяться в паузу ПОСЛЕ него
  - Адаптивный режим позволяет использовать больше паузы для сегментов,
    которым не хватает времени (по фактическим данным TTS)
  - Минимальный зазор между сегментами сохраняется для «дыхания»
"""
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# ── Базовый режим (для SmartTranslator, до TTS) ──────────────────────────
GAP_USAGE_RATIO = 0.85        # Использовать 85% паузы (15% — «воздух»)
MAX_GAP_EXTENSION_SEC = 2.0   # Максимум +2с расширения
MIN_GAP_TO_USE_SEC = 0.1      # Игнорировать паузы < 100мс

# ── Адаптивный режим (для VideoMaker, после TTS) ─────────────────────────
ADAPTIVE_GAP_RATIO = 0.95         # В адаптивном режиме — до 95% паузы
ADAPTIVE_MAX_EXTENSION_SEC = 3.5  # До +3.5с расширения
MIN_BREATHING_GAP_SEC = 0.05      # Минимум 50мс между сегментами


def compute_effective_durations(
    segments: List[Dict],
    total_duration_sec: Optional[float] = None
) -> List[Dict]:
    """
    Для каждого сегмента вычисляет effective_duration с учётом паузы после него.

    Используется на этапе перевода (до TTS) — консервативные лимиты.

    Args:
        segments: Список сегментов с ключами 'start', 'end' (секунды).
        total_duration_sec: Общая длительность таймлайна.

    Returns:
        Тот же список, каждый сегмент получает:
          - 'effective_duration': float — эффективная длительность (сек)
          - 'gap_extension': float — заимствовано из паузы (сек)
          - 'gap_after': float — полная пауза после сегмента (сек)
    """
    if not segments:
        return segments

    sorted_segs = sorted(segments, key=lambda s: float(s.get("start", 0)))

    for i, seg in enumerate(sorted_segs):
        start = float(seg.get("start", 0))
        end = float(seg.get("end", start + 1.0))
        original_duration = end - start

        # Полная пауза до следующего сегмента
        if i < len(sorted_segs) - 1:
            next_start = float(sorted_segs[i + 1].get("start", 0))
            gap_after = max(0.0, next_start - end)
        elif total_duration_sec is not None:
            gap_after = max(0.0, total_duration_sec - end)
        else:
            gap_after = 0.0

        # Доступное расширение
        if gap_after >= MIN_GAP_TO_USE_SEC:
            usable_gap = min(gap_after * GAP_USAGE_RATIO, MAX_GAP_EXTENSION_SEC)
        else:
            usable_gap = 0.0

        seg['effective_duration'] = original_duration + usable_gap
        seg['gap_extension'] = usable_gap
        seg['gap_after'] = gap_after

    return sorted_segs


def compute_adaptive_durations(
    segments: List[Dict],
    total_duration_sec: Optional[float] = None
) -> List[Dict]:
    """
    Адаптивное вычисление таймингов с учётом фактических длительностей TTS.

    Двухпроходный алгоритм:
      1. Базовое расширение (как compute_effective_durations)
      2. Для сегментов с actual_audio_duration > effective_duration —
         дополнительное расширение в оставшуюся часть паузы

    Используется в VideoMaker после генерации TTS.

    Args:
        segments: Сегменты с 'start', 'end', опционально 'actual_audio_duration'
        total_duration_sec: Общая длительность видео

    Returns:
        Обновлённые сегменты с оптимизированными effective_duration
    """
    if not segments:
        return segments

    sorted_segs = sorted(segments, key=lambda s: float(s.get("start", 0)))

    # Проходим по всем сегментам
    for i, seg in enumerate(sorted_segs):
        start = float(seg.get("start", 0))
        end = float(seg.get("end", start + 1.0))
        original_duration = end - start

        # Полная пауза после сегмента
        if i < len(sorted_segs) - 1:
            next_start = float(sorted_segs[i + 1].get("start", 0))
            gap_after = max(0.0, next_start - end)
        elif total_duration_sec is not None:
            gap_after = max(0.0, total_duration_sec - end)
        else:
            gap_after = 0.0

        seg['gap_after'] = gap_after

        # Базовое расширение (консервативное)
        if gap_after >= MIN_GAP_TO_USE_SEC:
            basic_extension = min(gap_after * GAP_USAGE_RATIO, MAX_GAP_EXTENSION_SEC)
        else:
            basic_extension = 0.0

        basic_effective = original_duration + basic_extension

        # Адаптивное расширение: если фактическое аудио длиннее базового бюджета
        actual_duration = seg.get('actual_audio_duration', 0)

        if actual_duration > basic_effective and gap_after > basic_extension:
            # Сколько ещё нужно?
            deficit = actual_duration - basic_effective
            # Сколько паузы ещё доступно? (до 95% общей, минус уже использовано)
            max_total_usable = gap_after * ADAPTIVE_GAP_RATIO - MIN_BREATHING_GAP_SEC
            additional_available = max(0.0, max_total_usable - basic_extension)
            # Ограничиваем общее расширение
            additional = min(deficit, additional_available, ADAPTIVE_MAX_EXTENSION_SEC - basic_extension)
            additional = max(0.0, additional)

            effective = basic_effective + additional
            total_extension = basic_extension + additional

            if additional > 0:
                logger.debug(
                    f"Seg {i}: adaptive +{additional:.2f}s "
                    f"(actual={actual_duration:.2f}s, basic={basic_effective:.2f}s, "
                    f"adaptive={effective:.2f}s)"
                )
        else:
            effective = basic_effective
            total_extension = basic_extension

        seg['effective_duration'] = effective
        seg['gap_extension'] = total_extension

    return sorted_segs


def estimate_timing_pressure(segments: List[Dict]) -> Dict[str, float]:
    """
    Оценивает «давление» тайминга: насколько сегменты укладываются в бюджет.

    Returns:
        Словарь со статистикой:
          - 'avg_pressure': средний ratio (actual / effective), < 1.0 = запас
          - 'max_pressure': максимальный pressure ratio
          - 'over_budget_count': кол-во сегментов с actual > effective
          - 'over_budget_total_sec': суммарный дефицит (секунды)
    """
    pressures = []
    over_budget_sec = 0.0
    over_count = 0

    for seg in segments:
        actual = seg.get('actual_audio_duration', 0)
        effective = seg.get('effective_duration', 0)
        if actual > 0 and effective > 0:
            pressure = actual / effective
            pressures.append(pressure)
            if pressure > 1.0:
                over_budget_sec += actual - effective
                over_count += 1

    if not pressures:
        return {
            'avg_pressure': 0.0,
            'max_pressure': 0.0,
            'over_budget_count': 0,
            'over_budget_total_sec': 0.0,
        }

    return {
        'avg_pressure': sum(pressures) / len(pressures),
        'max_pressure': max(pressures),
        'over_budget_count': over_count,
        'over_budget_total_sec': over_budget_sec,
    }

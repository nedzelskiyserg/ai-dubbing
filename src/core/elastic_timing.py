# -*- coding: utf-8 -*-
"""
Elastic Timing — вычисление эффективных длительностей для дубляжа.

Профессиональный дублёр не привязан жёстко к таймингу каждой фразы —
он использует паузы между фразами, чтобы естественно закончить мысль.

Этот модуль вычисляет для каждого сегмента «эффективную длительность»:
    effective_duration = (end - start) + доступная_часть_паузы_до_следующего_сегмента

Используется в SmartTranslator (для бюджета символов перевода)
и в VideoMaker (для подгонки аудио к временной линии).
"""
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# --- Константы ---
GAP_USAGE_RATIO = 0.85        # Использовать 85% паузы (15% — «воздух» между фразами)
MAX_GAP_EXTENSION_SEC = 2.0   # Максимум +2с расширения (больше нарушает синхронизацию)
MIN_GAP_TO_USE_SEC = 0.1      # Игнорировать паузы < 100мс (не стоит усложнять)


def compute_effective_durations(
    segments: List[Dict],
    total_duration_sec: Optional[float] = None
) -> List[Dict]:
    """
    Для каждого сегмента вычисляет effective_duration с учётом паузы после него.

    Args:
        segments: Список сегментов с ключами 'start', 'end' (секунды).
                  Должен быть отсортирован по 'start' (сортируется защитно).
        total_duration_sec: Общая длительность таймлайна (например, длительность видео).
                            Если задана — последний сегмент может расшириться до конца.
                            Если None — последний сегмент не расширяется.

    Returns:
        Тот же список (модифицированный in-place), каждый сегмент получает:
          - 'effective_duration': float — эффективная длительность (сек), >= оригинальной
          - 'gap_extension': float — сколько времени заимствовано из паузы (сек)
    """
    if not segments:
        return segments

    sorted_segs = sorted(segments, key=lambda s: float(s.get("start", 0)))

    for i, seg in enumerate(sorted_segs):
        start = float(seg.get("start", 0))
        end = float(seg.get("end", start + 1.0))
        original_duration = end - start

        # Вычисляем паузу до следующего сегмента
        if i < len(sorted_segs) - 1:
            next_start = float(sorted_segs[i + 1].get("start", 0))
            gap_after = next_start - end
        elif total_duration_sec is not None:
            gap_after = total_duration_sec - end
        else:
            gap_after = 0.0

        # Защита от отрицательных пауз (перекрывающиеся сегменты)
        gap_after = max(0.0, gap_after)

        # Вычисляем доступное расширение
        if gap_after >= MIN_GAP_TO_USE_SEC:
            usable_gap = min(gap_after * GAP_USAGE_RATIO, MAX_GAP_EXTENSION_SEC)
        else:
            usable_gap = 0.0

        seg['effective_duration'] = original_duration + usable_gap
        seg['gap_extension'] = usable_gap

    return sorted_segs

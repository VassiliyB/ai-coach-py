# services/macro_replan.py
"""Пересчёт оставшихся недель макроплана по событиям (уровень 3 адаптации). Чистые функции, без I/O.

Повод: перерыв в беге от 7 дней или две недели подряд выполнены меньше чем на 70%. Тогда плановый
километраж с текущей недели заново выстраивается от фактического объёма: рост не больше 10% от лучшей
из двух предыдущих недель, каждая 4-я неделя разгрузочная, выше исходного плана объём не поднимается.
Подводка (фаза IV) сохраняет форму исходного плана в масштабе нового объёма. Фазы и их задачи не меняются.

Километраж здесь считает код, а не модель: пересчёт должен быть предсказуемым и всегда проходить правила.
"""
import math
from dataclasses import dataclass, replace
from typing import List, Optional

from schemas.plan import MacroPlan
from services.plan_validator import MAX_WEEKLY_GROWTH, TAPER_PHASE
from services.week_adaptation import LOW_COMPLIANCE, WeekAdjustment

BREAK_REPLAN_DAYS = 7          # перерыв в беге от недели: план пересчитывается
REPLAN_WINDOW_DAYS = 14        # перерыв ищется за последние 2 недели
LOW_WEEKS_FOR_REPLAN = 2       # столько недель подряд ниже LOW_COMPLIANCE
BREAK_START_SHARE = 0.75       # после перерыва первая неделя не больше 75% плановой
DELOAD_EVERY = 4               # каждая 4-я неделя от начала пересчёта разгрузочная
DELOAD_SHARE = 0.8
KM_ROUND = 0.5


@dataclass(frozen=True)
class Replan:
    macro: MacroPlan
    from_week: int
    reasons: List[str]
    old_km: List[float]

    def changes(self) -> List[tuple]:
        """(номер недели, было, стало) для недель, где километраж изменился."""
        return [
            (i + 1, old, new)
            for i, (old, new) in enumerate(zip(self.old_km, self.macro.weekly_km, strict=True))
            if i + 1 >= self.from_week and abs(old - new) > 0.01
        ]


def replan_reasons(gap_days: int, compliances: List[Optional[float]]) -> List[str]:
    """Поводы пересчитать план. compliances: выполнение последних недель по порядку (последняя текущая)."""
    reasons = []
    if gap_days >= BREAK_REPLAN_DAYS:
        reasons.append(f"перерыв в беге {gap_days} дн.")
    recent = compliances[-LOW_WEEKS_FOR_REPLAN:]
    if len(recent) == LOW_WEEKS_FOR_REPLAN and all(c is not None and c < LOW_COMPLIANCE for c in recent):
        shares = ", ".join(f"{c:.0%}" for c in recent)
        reasons.append(f"{LOW_WEEKS_FOR_REPLAN} недели подряд выполнены меньше чем на {LOW_COMPLIANCE:.0%} ({shares})")
    return reasons


def _round_km(km: float) -> float:
    """Вниз до 0.5 км: округление вверх могло бы превысить предел роста."""
    return max(KM_ROUND, math.floor(km / KM_ROUND + 1e-9) * KM_ROUND)


def replan_weekly_km(old_km: List[float], phases_of_week: List[int], from_week: int, start_km: float) -> List[float]:
    """Новый километраж: недели до from_week без изменений, дальше рост от start_km по правилам."""
    if not 1 <= from_week <= len(old_km):
        return list(old_km)
    first = from_week - 1
    new = list(old_km[:first])
    taper_start = next(
        (i for i, ph in enumerate(phases_of_week) if ph == TAPER_PHASE and i >= first), len(old_km),
    )

    for i in range(first, taper_start):
        if i == first:
            km = start_km
        else:
            reference = max(new[max(first, i - 2):i])
            growth_cap = reference * (1 + MAX_WEEKLY_GROWTH)
            if new[i - 1] >= old_km[i - 1] - 0.01:
                km = min(old_km[i], growth_cap)       # догнали план: дальше по нему, включая его разгрузки
            elif (i - first) % DELOAD_EVERY == DELOAD_EVERY - 1:
                km = reference * DELOAD_SHARE         # долгое восстановление объёма: своя разгрузка
            else:
                km = growth_cap
        new.append(min(_round_km(km), old_km[i]))

    # Подводка: форма исходного плана в масштабе последней недели перед ней, объём не растёт
    if taper_start < len(old_km):
        if taper_start > first:
            scale = new[taper_start - 1] / old_km[taper_start - 1]
        else:
            scale = min(1.0, start_km / old_km[first])
        for i in range(taper_start, len(old_km)):
            km = _round_km(old_km[i] * scale)
            if i > 0:
                km = min(km, new[i - 1])
            new.append(min(km, old_km[i]))
    return new


def replan_macro(macro: MacroPlan, from_week: int, start_km: float, reasons: List[str]) -> Replan:
    """Макроплан с пересчитанным километражем начиная с недели from_week (её объём start_km)."""
    new_km = replan_weekly_km(macro.weekly_km, macro.week_phase_numbers(), from_week, start_km)
    return Replan(
        macro=macro.model_copy(update={"weekly_km": new_km}),
        from_week=from_week,
        reasons=reasons,
        old_km=list(macro.weekly_km),
    )


def start_km_after(plan_target_km: float, adjusted_km: float, gap_days: int) -> float:
    """Объём первой недели пересчёта: поправка недели, а после перерыва не больше BREAK_START_SHARE плана."""
    if gap_days >= BREAK_REPLAN_DAYS:
        return min(adjusted_km, plan_target_km * BREAK_START_SHARE)
    return adjusted_km


def adjustment_after_replan(adjustment: WeekAdjustment, replan: Replan, gap_days: int) -> WeekAdjustment:
    """Поправка недели, согласованная с пересчитанным планом: тот же километраж, после перерыва одна качественная."""
    max_quality = adjustment.max_quality
    reasons = list(adjustment.reasons)
    if gap_days >= BREAK_REPLAN_DAYS:
        max_quality = 1 if max_quality is None else min(max_quality, 1)
        reasons.append(f"после перерыва {gap_days} дн. объём восстанавливается постепенно")
    return replace(
        adjustment,
        target_km=replan.macro.weekly_km[replan.from_week - 1],
        max_quality=max_quality,
        reasons=reasons,
    )

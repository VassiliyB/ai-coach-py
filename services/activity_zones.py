# services/activity_zones.py
"""Зона завершённой тренировки по темпу и пульсу. Чистые функции, без I/O.

Модель в /analyze получает готовый расчёт и только интерпретирует его: проценты и зоны считает код.
"""
from dataclasses import dataclass
from typing import Optional, Tuple

from services.coach_service import TrainingZones, format_pace
from services.plan_paces import HR_FRACTIONS, hr_range_for_zone

ZONE_NAMES = {
    "below_E": "медленнее зоны E (восстановительный бег)",
    "E": "зона E (лёгкий бег)",
    "M": "зона M (марафонский темп, серая зона)",
    "T": "зона T (пороговый темп)",
    "I": "зона I (интервалы МПК)",
    "R": "зона R (повторы)",
}
HR_ZONE_NAMES = {
    "below_E": "ниже зоны E (восстановление)",
    "E": "зона E (лёгкий бег)",
    "M": "зона M (серая зона)",
    "T": "зона T (порог)",
    "I": "зона I (МПК)",
}
GRAY_ZONE = "M"


def pace_zone(pace_sec: Optional[float], zones: Optional[TrainingZones]) -> Optional[str]:
    """Зона Дэниелса по среднему темпу (сек/км). Быстрее лёгкого диапазона берётся ближайшая зона."""
    if not pace_sec or pace_sec <= 0 or zones is None:
        return None
    if pace_sec > zones.easy.slow:
        return "below_E"
    if pace_sec >= zones.easy.fast:
        return "E"
    references = [
        ("E", zones.easy.fast), ("M", zones.marathon.fast), ("T", zones.threshold.fast),
        ("I", zones.interval.fast), ("R", zones.repetition.fast),
    ]
    return min(references, key=lambda ref: abs(ref[1] - pace_sec))[0]


def hr_zone(avg_hr: Optional[float], max_hr: Optional[int]) -> Optional[Tuple[str, int]]:
    """(зона, процент от ЧССmax) по среднему пульсу. Промежутки между диапазонами относятся к зоне ниже."""
    if not avg_hr or not max_hr or max_hr <= 0:
        return None
    fraction = avg_hr / max_hr
    zone = "below_E"
    for name in ("E", "M", "T", "I"):
        if fraction >= HR_FRACTIONS[name][0]:
            zone = name
    return zone, round(fraction * 100)


@dataclass(frozen=True)
class ActivityZones:
    pace_zone: Optional[str]
    hr_zone: Optional[str]
    hr_pct: Optional[int]

    @property
    def gray_zone(self) -> bool:
        return GRAY_ZONE in (self.pace_zone, self.hr_zone)

    @property
    def mismatch(self) -> bool:
        """Темп и пульс указывают на разные зоны (например, лёгкий темп при высоком пульсе)."""
        if not self.pace_zone or not self.hr_zone:
            return False
        return self.pace_zone != self.hr_zone


def classify_activity(
    pace_sec: Optional[float], avg_hr: Optional[float], zones: Optional[TrainingZones], max_hr: Optional[int],
) -> ActivityZones:
    hr = hr_zone(avg_hr, max_hr)
    return ActivityZones(
        pace_zone=pace_zone(pace_sec, zones),
        hr_zone=hr[0] if hr else None,
        hr_pct=hr[1] if hr else None,
    )


def format_activity_zones(
    result: ActivityZones,
    pace_sec: Optional[float],
    avg_hr: Optional[float],
    zones: Optional[TrainingZones],
    max_hr: Optional[int],
    peak_hr: Optional[float] = None,
) -> str:
    """Текст расчёта для промпта /analyze. peak_hr: максимальный пульс за тренировку."""
    lines = ["РАСЧЁТ ЗОН ТРЕНИРОВКИ (выполнен кодом):"]
    if result.pace_zone:
        lines.append(
            f"- Средний темп {format_pace(pace_sec)} /км: {ZONE_NAMES[result.pace_zone]}. "
            f"Лёгкий диапазон атлета: {zones.easy}."
        )
    else:
        lines.append("- Зона по темпу не определена (нет VDOT или темпа).")
    if result.hr_zone:
        lines.append(
            f"- Средний пульс {round(avg_hr)} уд/мин = {result.hr_pct}% от ЧССmax {max_hr}: "
            f"{HR_ZONE_NAMES[result.hr_zone]}."
        )
    else:
        lines.append("- Зона по пульсу не определена (нет пульса или ЧССmax).")
    peak = hr_zone(peak_hr, max_hr)
    if peak:
        lines.append(
            f"- Максимальный пульс за тренировку {round(peak_hr)} уд/мин = {peak[1]}% от ЧССmax: "
            f"{HR_ZONE_NAMES[peak[0]]}."
        )
    easy_hr = hr_range_for_zone("E", max_hr)
    if easy_hr:
        lines.append(f"- Пульс лёгкого бега (зона E) для атлета: {easy_hr[0]}–{easy_hr[1]} уд/мин.")
    if result.gray_zone:
        lines.append("- Тренировка попала в серую зону (умеренная интенсивность по Фицджеральду).")
    if result.mismatch:
        lines.append("- Темп и пульс указывают на разные зоны.")
    return "\n".join(lines)

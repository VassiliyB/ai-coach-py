# services/plan_paces.py
"""Подстановка темпов: зону дня определяет тип тренировки, темп берётся из рассчитанных зон VDOT."""
from typing import Optional

from schemas.plan import PlannedDay
from services.coach_service import PaceRange, TrainingZones

_ZONE_ATTR = {
    "E": "easy",
    "M": "marathon",
    "T": "threshold",
    "I": "interval",
    "R": "repetition",
}


def pace_for_zone(zone: Optional[str], zones: Optional[TrainingZones]) -> Optional[PaceRange]:
    if not zone or zones is None:
        return None
    attr = _ZONE_ATTR.get(zone)
    return getattr(zones, attr) if attr else None


def pace_text(day: PlannedDay, zones: Optional[TrainingZones]) -> str:
    """Темп дня в виде '4:15 /км' или '5:30–6:10 /км'. Пустая строка, если темп неприменим или зон нет."""
    pace = pace_for_zone(day.zone, zones)
    return str(pace) if pace else ""
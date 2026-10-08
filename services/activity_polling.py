# services/activity_polling.py
"""Какие новые тренировки разбирать при опросе Garmin. Чистые функции, без I/O."""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

FETCH_LIMIT = 10                # сколько последних тренировок смотреть за один опрос
MAX_ANALYSIS_AGE = timedelta(hours=24)  # старые тренировки помечаются молча: например, после простоя бота


def parse_gmt(value: Optional[str]) -> Optional[datetime]:
    """Время старта из Garmin ('2026-10-08 05:12:33', допускается ISO с 'T' и 'Z') -> datetime в UTC."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", ""))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def activity_id(activity: Dict[str, Any]) -> Optional[str]:
    value = activity.get("activity_id")
    return value if value and value != "None" else None


def select_for_analysis(new_activities: List[Dict[str, Any]], now: datetime) -> List[Dict[str, Any]]:
    """Новые беговые тренировки не старше MAX_ANALYSIS_AGE, от старых к новым.

    Тренировка без времени старта не разбирается: её возраст неизвестен.
    """
    fresh = []
    for act in new_activities:
        started = parse_gmt(act.get("start_time_gmt"))
        if act.get("is_running") and started is not None and timedelta(0) <= now - started <= MAX_ANALYSIS_AGE:
            fresh.append((started, act))
    return [act for _, act in sorted(fresh, key=lambda pair: pair[0])]

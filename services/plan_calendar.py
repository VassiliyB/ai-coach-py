# services/plan_calendar.py
"""Календарь плана: границы недель и номер недели подготовки. Чистые функции, без I/O."""
from datetime import date, timedelta
from typing import Optional, Tuple

DATE_FORMAT = "%d.%m.%Y"


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def next_week_dates(today: date) -> Tuple[date, date]:
    """Понедельник и воскресенье следующей недели (в воскресенье это уже завтрашняя неделя)."""
    monday = monday_of(today) + timedelta(days=7)
    return monday, monday + timedelta(days=6)


def plan_total_weeks(today: date, race_date: date) -> int:
    """Число недель плана: от следующего понедельника до недели забега включительно.

    Считается по тому же календарю, что и plan_week_number, поэтому первая рассылка
    после создания плана получает неделю №1, а неделя забега последний номер.
    """
    first_monday, _ = next_week_dates(today)
    return (monday_of(race_date) - first_monday).days // 7 + 1


def plan_week_number(race_date: date, total_weeks: int, week_monday: date) -> Optional[int]:
    """Номер недели подготовки (1..total_weeks) для недели, начинающейся в week_monday.

    Последняя неделя плана та, на которую приходится забег: счёт ведётся от неё назад,
    поэтому номер не зависит от дня недели, в который пользователь создал план.
    Неделя до начала плана считается первой. После недели забега возвращается None: план завершён.
    """
    weeks_before_race = (monday_of(race_date) - monday_of(week_monday)).days // 7
    number = total_weeks - weeks_before_race
    if number > total_weeks:
        return None
    return max(1, number)

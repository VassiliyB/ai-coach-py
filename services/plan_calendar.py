# services/plan_calendar.py
"""Календарь плана: границы недель и номер недели подготовки. Чистые функции, без I/O."""
from datetime import date, timedelta
from typing import List, Optional, Tuple

DATE_FORMAT = "%d.%m.%Y"


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def next_week_dates(today: date) -> Tuple[date, date]:
    """Понедельник и воскресенье следующей недели (в воскресенье это уже завтрашняя неделя)."""
    monday = monday_of(today) + timedelta(days=7)
    return monday, monday + timedelta(days=6)


def intro_days(today: date) -> List[date]:
    """Вводные дни до старта плана: с завтрашнего дня до воскресенья текущей недели.

    План начинается со следующего понедельника, эти дни иначе остались бы без тренировок.
    В воскресенье список пуст: неделя №1 начинается завтра.
    """
    sunday = monday_of(today) + timedelta(days=6)
    return [today + timedelta(days=i) for i in range(1, (sunday - today).days + 1)]


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


def plan_start_monday(race_date: date, total_weeks: int) -> date:
    """Понедельник недели №1: от недели забега назад на total_weeks - 1 недель."""
    return monday_of(race_date) - timedelta(weeks=max(1, total_weeks) - 1)


def current_plan_week(today: date, race_date: date, total_weeks: int) -> Optional[int]:
    """Номер текущей недели плана (1..total_weeks). None до начала плана и после забега."""
    if today > race_date or today < plan_start_monday(race_date, total_weeks):
        return None
    return plan_week_number(race_date, total_weeks, today)

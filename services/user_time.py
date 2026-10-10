# services/user_time.py
"""Часовой пояс пользователя: разбор, определение по Garmin, локальное время. Чистые функции, без I/O.

Пояс хранится строкой: имя IANA ('Asia/Almaty') или фиксированное смещение ('UTC+05:00').
Имя IANA учитывает переход на летнее время, смещение нет.
"""
import re
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

WEEKLY_SEND_WEEKDAY = 6   # воскресенье
WEEKLY_SEND_HOUR = 15     # с 15:00 по местному времени пользователя

_OFFSET_RE = re.compile(r"^(?:UTC|GMT)?\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?$", re.IGNORECASE)
MAX_OFFSET = timedelta(hours=14)


def format_offset(offset: timedelta) -> str:
    """timedelta(hours=5) -> 'UTC+05:00'."""
    sign = "-" if offset < timedelta(0) else "+"
    minutes = abs(int(offset.total_seconds())) // 60
    return f"UTC{sign}{minutes // 60:02d}:{minutes % 60:02d}"


def normalize_timezone(value: Optional[str]) -> Optional[str]:
    """Ввод пользователя или значение из БД -> каноническая строка пояса. None, если не распознано.

    Принимает имя IANA ('Europe/Moscow'), 'UTC', смещение ('+5', 'UTC+05:30', '-3').
    """
    text = (value or "").strip()
    if not text:
        return None
    if text.upper() in ("UTC", "GMT"):
        return "UTC"
    match = _OFFSET_RE.match(text)
    if match:
        sign, hours, minutes = match.group(1), int(match.group(2)), int(match.group(3) or 0)
        offset = timedelta(hours=hours, minutes=minutes)
        if minutes >= 60 or offset > MAX_OFFSET:
            return None
        return format_offset(-offset if sign == "-" else offset)
    if "/" not in text:  # отсекаем сокращения вроде 'MSK': ZoneInfo их не знает или трактует неоднозначно
        return None
    try:
        ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return text


def to_tzinfo(value: Optional[str], default: str = "UTC") -> tzinfo:
    """Строка пояса -> tzinfo. Нераспознанное значение заменяется поясом по умолчанию."""
    canonical = normalize_timezone(value) or normalize_timezone(default) or "UTC"
    if canonical == "UTC":
        return timezone.utc
    if canonical.startswith("UTC"):
        match = _OFFSET_RE.match(canonical)
        offset = timedelta(hours=int(match.group(2)), minutes=int(match.group(3) or 0))
        return timezone(-offset if match.group(1) == "-" else offset)
    return ZoneInfo(canonical)


REMINDER_OFF = -1
_REMINDER_OFF_WORDS = {"выкл", "выключить", "нет", "off"}
_REMINDER_HOUR_RE = re.compile(r"^(\d{1,2})(?:[:.](\d{2}))?$")


def parse_reminder_hour(text: str) -> Optional[int]:
    """'6', '06:00', '7.00' -> час 0–23; 'выкл' -> REMINDER_OFF. None: не распознано или минуты не :00
    (напоминания проверяются раз в час)."""
    text = (text or "").strip().lower()
    if text in _REMINDER_OFF_WORDS:
        return REMINDER_OFF
    match = _REMINDER_HOUR_RE.match(text)
    if not match or (match.group(2) not in (None, "00")):
        return None
    hour = int(match.group(1))
    return hour if 0 <= hour <= 23 else None


def local_now(tz: tzinfo, now_utc: Optional[datetime] = None) -> datetime:
    return (now_utc or datetime.now(timezone.utc)).astimezone(tz)


def local_today(tz: tzinfo, now_utc: Optional[datetime] = None) -> date:
    return local_now(tz, now_utc).date()


def local_day_start(tz: tzinfo, now_utc: Optional[datetime] = None) -> datetime:
    """Местная полночь сегодня (aware): с неё считаются дневные лимиты."""
    today = local_today(tz, now_utc)
    return datetime(today.year, today.month, today.day, tzinfo=tz)


def local_month_start(tz: tzinfo, now_utc: Optional[datetime] = None) -> datetime:
    """Местная полночь 1-го числа текущего месяца (aware): расход за месяц."""
    today = local_today(tz, now_utc)
    return datetime(today.year, today.month, 1, tzinfo=tz)


def is_weekly_send_time(local: datetime) -> bool:
    """Пора ли отправлять неделю: воскресенье с 15:00 по местному времени до конца дня.

    Проверка идёт каждый час, повторную отправку отсекает проверка «неделя уже создана».
    Если бот был выключен в 15:00, неделя уйдёт при первой проверке позже в тот же день.
    """
    return local.weekday() == WEEKLY_SEND_WEEKDAY and local.hour >= WEEKLY_SEND_HOUR


def offset_from_activity(start_local: Optional[str], start_gmt: Optional[str]) -> Optional[str]:
    """Смещение пояса по одной тренировке Garmin: местное время старта минус время в UTC.

    Округляется до 15 минут (поясов с другим шагом нет). None, если времени нет или смещение невозможно.
    """
    try:
        local = datetime.fromisoformat(str(start_local).replace("Z", ""))
        gmt = datetime.fromisoformat(str(start_gmt).replace("Z", ""))
    except (TypeError, ValueError):
        return None
    minutes = round((local - gmt).total_seconds() / 60 / 15) * 15
    offset = timedelta(minutes=minutes)
    if abs(offset) > MAX_OFFSET:
        return None
    return format_offset(offset)

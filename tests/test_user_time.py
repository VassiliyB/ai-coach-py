from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from services.user_time import (
    format_offset, is_weekly_send_time, local_today, normalize_timezone, offset_from_activity, to_tzinfo,
)


@pytest.mark.parametrize("value,expected", [
    ("Asia/Almaty", "Asia/Almaty"),
    ("  Europe/Moscow ", "Europe/Moscow"),
    ("UTC", "UTC"),
    ("gmt", "UTC"),
    ("+5", "UTC+05:00"),
    ("UTC+5", "UTC+05:00"),
    ("UTC+05:30", "UTC+05:30"),
    ("-3", "UTC-03:00"),
    ("GMT-03:00", "UTC-03:00"),
])
def test_normalize_timezone_accepts(value, expected):
    assert normalize_timezone(value) == expected


@pytest.mark.parametrize("value", [None, "", "MSK", "Mars/Olympus", "+15", "+5:75", "пять"])
def test_normalize_timezone_rejects(value):
    assert normalize_timezone(value) is None


def test_to_tzinfo():
    assert to_tzinfo("Asia/Almaty") == ZoneInfo("Asia/Almaty")
    assert to_tzinfo("UTC+05:30").utcoffset(None) == timedelta(hours=5, minutes=30)
    assert to_tzinfo("UTC-03:00").utcoffset(None) == timedelta(hours=-3)
    assert to_tzinfo(None, default="Europe/Moscow") == ZoneInfo("Europe/Moscow")
    assert to_tzinfo("мусор", default="UTC") == timezone.utc


def test_local_today_crosses_midnight():
    now_utc = datetime(2026, 10, 11, 20, 30, tzinfo=timezone.utc)      # воскресенье 20:30 UTC
    assert local_today(to_tzinfo("UTC+05:00"), now_utc) == date(2026, 10, 12)   # в UTC+5 уже понедельник
    assert local_today(to_tzinfo("UTC-03:00"), now_utc) == date(2026, 10, 11)


@pytest.mark.parametrize("local,expected", [
    (datetime(2026, 10, 11, 14, 59), False),   # воскресенье до 15:00
    (datetime(2026, 10, 11, 15, 0), True),
    (datetime(2026, 10, 11, 23, 30), True),    # бот был выключен в 15:00: догоняем вечером
    (datetime(2026, 10, 12, 15, 0), False),    # понедельник
    (datetime(2026, 10, 10, 18, 0), False),    # суббота
])
def test_is_weekly_send_time(local, expected):
    assert is_weekly_send_time(local) is expected


def test_same_utc_moment_differs_by_user():
    now_utc = datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)
    assert is_weekly_send_time(now_utc.astimezone(to_tzinfo("UTC+05:00")))       # 15:00 у пользователя
    assert not is_weekly_send_time(now_utc.astimezone(to_tzinfo("Europe/Moscow")))  # 13:00 в Москве


def test_offset_from_activity():
    # тренировка из живого опроса: старт 00:09 UTC, в Алматы 05:09
    assert offset_from_activity("2026-10-07 05:09:22", "2026-10-07 00:09:22") == "UTC+05:00"
    assert offset_from_activity("2026-10-07 06:39:22", "2026-10-07 01:09:22") == "UTC+05:30"
    assert offset_from_activity("2026-10-06 21:09:22", "2026-10-07 00:09:22") == "UTC-03:00"
    assert offset_from_activity("2026-10-07 05:09:51", "2026-10-07 00:09:22") == "UTC+05:00"  # секунды не мешают


def test_offset_from_activity_bad_input():
    assert offset_from_activity(None, "2026-10-07 00:09:22") is None
    assert offset_from_activity("2026-10-08 05:00:00", "2026-10-07 00:00:00") is None   # больше 14 часов


def test_format_offset():
    assert format_offset(timedelta(hours=-9, minutes=-30)) == "UTC-09:30"
    assert format_offset(timedelta(0)) == "UTC+00:00"


def test_detect_utc_offset_uses_latest_activity_with_times():
    from clients.garmin.analytics import detect_utc_offset
    runs = [
        {"startTimeLocal": None, "startTimeGMT": "2026-10-07 00:09:22"},                     # без местного времени
        {"startTimeLocal": "2026-10-04 15:32:05", "startTimeGMT": "2026-10-04 10:32:05"},   # самая свежая с временем
        {"startTimeLocal": "2026-07-01 10:00:00", "startTimeGMT": "2026-07-01 06:00:00"},
    ]
    assert detect_utc_offset(runs) == "UTC+05:00"
    assert detect_utc_offset([]) is None

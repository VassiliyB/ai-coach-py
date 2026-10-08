from datetime import datetime, timedelta, timezone

from services.activity_polling import activity_id, parse_gmt, select_for_analysis

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


def act(aid, hours_ago, running=True, start=None):
    start_gmt = start if start is not None else (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%d %H:%M:%S")
    return {"activity_id": aid, "is_running": running, "start_time_gmt": start_gmt}


def test_parse_gmt_formats():
    expected = datetime(2026, 10, 8, 5, 12, 33, tzinfo=timezone.utc)
    assert parse_gmt("2026-10-08 05:12:33") == expected
    assert parse_gmt("2026-10-08T05:12:33Z") == expected
    assert parse_gmt("2026-10-08T05:12:33.0") == expected


def test_parse_gmt_bad_values():
    assert parse_gmt(None) is None
    assert parse_gmt("") is None
    assert parse_gmt("вчера") is None


def test_activity_id_skips_missing():
    assert activity_id({"activity_id": "123"}) == "123"
    assert activity_id({"activity_id": "None"}) is None   # str(None) из parse_last_activity
    assert activity_id({}) is None


def test_selects_fresh_runs_oldest_first():
    new = [act("3", 1), act("2", 5)]
    assert [a["activity_id"] for a in select_for_analysis(new, NOW)] == ["2", "3"]


def test_skips_non_running_and_old():
    new = [act("1", 2, running=False), act("2", 30), act("3", 3)]
    assert [a["activity_id"] for a in select_for_analysis(new, NOW)] == ["3"]


def test_skips_unknown_start_and_future():
    new = [act("1", 1, start=""), act("2", -2)]   # без времени старта и «из будущего» (сбой часов)
    assert select_for_analysis(new, NOW) == []


def test_boundary_24_hours_is_included():
    assert [a["activity_id"] for a in select_for_analysis([act("1", 24)], NOW)] == ["1"]

import logging
from datetime import date

from services.backup_watch import LAST_DATE_FILE, MAX_AGE_DAYS, backup_age_days, check_backups, read_last_date

TODAY = date(2026, 10, 9)


def test_read_last_date(tmp_path):
    assert read_last_date(tmp_path) is None                          # копий ещё не было
    (tmp_path / LAST_DATE_FILE).write_text("2026-10-08\n", encoding="utf-8")
    assert read_last_date(tmp_path) == date(2026, 10, 8)
    (tmp_path / LAST_DATE_FILE).write_text("мусор", encoding="utf-8")
    assert read_last_date(tmp_path) is None


def test_age():
    assert backup_age_days(None, TODAY) is None
    assert backup_age_days(date(2026, 10, 7), TODAY) == 2


def test_check_logs_error_only_when_stale(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="services.backup_watch")
    assert check_backups(tmp_path, TODAY) is None
    assert [r.levelname for r in caplog.records] == ["WARNING"]      # первые сутки: не тревога

    caplog.clear()
    (tmp_path / LAST_DATE_FILE).write_text(f"2026-10-0{9 - MAX_AGE_DAYS}", encoding="utf-8")
    assert check_backups(tmp_path, TODAY) == MAX_AGE_DAYS
    assert caplog.records == []                                      # два дня ещё допустимо

    (tmp_path / LAST_DATE_FILE).write_text("2026-10-05", encoding="utf-8")
    assert check_backups(tmp_path, TODAY) == 4
    assert [r.levelname for r in caplog.records] == ["ERROR"]        # уйдёт уведомлением админам

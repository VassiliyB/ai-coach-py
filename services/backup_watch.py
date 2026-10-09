# services/backup_watch.py
"""Проверка, что резервное копирование БД не остановилось (копии делает сервис backup, scripts/backup.sh).

Скрипт после каждой успешной проверки пишет дату в .last_date, даже если данные не изменились и новая
копия не нужна. Бот раз в день смотрит на эту дату: если она старше MAX_AGE_DAYS, пишет ERROR в лог,
и уведомление уходит админам (services.admin_alerts). Файла ещё нет (первые сутки после развёртывания)
— только WARNING.
"""
import logging
from datetime import date
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MAX_AGE_DAYS = 2         # копия раз в сутки: два дня без неё уже не случайность
LAST_DATE_FILE = ".last_date"


def read_last_date(backup_dir: Path) -> Optional[date]:
    try:
        return date.fromisoformat((backup_dir / LAST_DATE_FILE).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def backup_age_days(last: Optional[date], today: date) -> Optional[int]:
    return (today - last).days if last is not None else None


def check_backups(backup_dir: Path, today: date) -> Optional[int]:
    """Возраст последней копии в днях (None: копий ещё не было). Сам пишет в лог WARNING или ERROR."""
    age = backup_age_days(read_last_date(backup_dir), today)
    if age is None:
        logger.warning("Резервных копий БД ещё нет (%s): проверьте сервис backup", backup_dir)
    elif age > MAX_AGE_DAYS:
        logger.error(
            "Резервная копия БД не делалась %s дн.: проверьте docker compose logs backup", age,
        )
    return age

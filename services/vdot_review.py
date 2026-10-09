# services/vdot_review.py
"""Пересмотр VDOT раз в 4 недели плана (уровень 2 адаптации). Чистые функции, без I/O.

По Дэниелсу на одном VDOT тренируются 4–6 недель, потом его поднимают, если форма это подтверждает.
Подтверждение: лучшая пробежка от 3 км за последние недели даёт VDOT выше текущего. Повышение за один
пересмотр ограничено: тренировочная пробежка не забег, а резкий скачок темпов опаснее недооценки.
После перерыва в беге VDOT снижается по таблице потерь формы Дэниелса.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional

from clients.garmin.analytics import find_best_effort
from services.coach_service import MAX_VDOT, MIN_VDOT

REVIEW_EVERY_WEEKS = 4           # пересмотр перед неделями 5, 9, 13, ...
MIN_DAYS_BETWEEN_REVIEWS = 21    # повторная генерация той же недели не пересматривает VDOT второй раз
RECENT_WEEKS = 6                 # окно для лучшей пробежки
GAP_WINDOW_WEEKS = 4             # окно для поиска перерыва
MIN_RAISE = 0.5                  # меньшее превышение это шум
MAX_RAISE = 2.0                  # за один пересмотр VDOT растёт не больше
BREAK_MIN_DAYS = 6               # перерыв до 5 дней форму не снижает (Дэниелс)
BREAK_LOSS_PER_DAY = 0.0025      # 6–28 дней: около 0.25% за день, к 4 неделям ~7%
BREAK_MAX_LOSS = 0.10


class VdotChange(str, Enum):
    RAISED = "raised"
    LOWERED = "lowered"
    KEPT = "kept"


@dataclass(frozen=True)
class VdotReview:
    old: float
    new: float
    change: VdotChange
    reason: str                       # для пользователя, числа от кода
    best_recent: Optional[float] = None
    gap_days: int = 0


def is_review_week(week_number: int) -> bool:
    """Неделя, перед которой пересматривается VDOT: 5, 9, 13, ... (после каждых 4 недель плана)."""
    return week_number > 1 and (week_number - 1) % REVIEW_EVERY_WEEKS == 0


def review_due(week_number: int, last_review: Optional[date], today: date) -> bool:
    if not is_review_week(week_number):
        return False
    return last_review is None or (today - last_review).days >= MIN_DAYS_BETWEEN_REVIEWS


def _run_date(raw: Dict[str, Any]) -> Optional[date]:
    try:
        return datetime.fromisoformat(str(raw.get("startTimeLocal")).replace("Z", "")).date()
    except ValueError:
        return None


def longest_gap_days(run_dates: Iterable[date], start: date, end: date) -> int:
    """Самый длинный промежуток без бега (в днях) внутри [start, end], включая края окна."""
    days = sorted({d for d in run_dates if start <= d <= end})
    edges = [start - timedelta(days=1)] + days + [end + timedelta(days=1)]
    return max(b - a for a, b in zip(edges, edges[1:], strict=False)).days - 1


def break_loss(gap_days: int) -> float:
    """Доля потери VDOT после перерыва (приближение к таблице Дэниелса)."""
    if gap_days < BREAK_MIN_DAYS:
        return 0.0
    return min(BREAK_MAX_LOSS, gap_days * BREAK_LOSS_PER_DAY)


def review_vdot(current: float, raw_runs: List[Dict[str, Any]], today: date) -> VdotReview:
    """Новый VDOT по пробежкам Garmin (сырой формат) за последние RECENT_WEEKS недель."""
    recent_start = today - timedelta(weeks=RECENT_WEEKS)
    recent = [r for r in raw_runs if (d := _run_date(r)) and recent_start <= d <= today]
    best = find_best_effort(recent)
    best_vdot = round(best["vdot"], 1) if best else None

    gap_start = today - timedelta(days=GAP_WINDOW_WEEKS * 7 - 1)   # ровно 4 недели, включая сегодня
    gap = longest_gap_days((d for r in raw_runs if (d := _run_date(r))), gap_start, today)
    loss = break_loss(gap)

    if loss:
        lowered = round(max(MIN_VDOT, current * (1 - loss)), 1)
        # Свежая пробежка после перерыва может показать, что форма потеряна меньше
        new = min(current, max(lowered, best_vdot or 0))
        if new < current:
            return VdotReview(
                current, new, VdotChange.LOWERED,
                f"перерыв в беге {gap} дн.: форма снижена на {(current - new) / current:.0%}",
                best_vdot, gap,
            )

    if best_vdot is not None and best_vdot >= current + MIN_RAISE:
        new = round(min(best_vdot, current + MAX_RAISE, MAX_VDOT), 1)
        capped = " (повышение ограничено)" if new < best_vdot else ""
        return VdotReview(
            current, new, VdotChange.RAISED,
            f"лучшая пробежка за {RECENT_WEEKS} нед. показывает VDOT {best_vdot:g}{capped}",
            best_vdot, gap,
        )

    reason = (
        f"лучшая пробежка за {RECENT_WEEKS} нед. показывает VDOT {best_vdot:g}: повышать рано"
        if best_vdot is not None else f"за {RECENT_WEEKS} нед. нет пробежек от 3 км для оценки"
    )
    return VdotReview(current, current, VdotChange.KEPT, reason, best_vdot, gap)


def vdot_after_sync(
    sync_vdot: Optional[float], current: Optional[float], reviewed_on: Optional[date],
) -> Optional[float]:
    """VDOT для сохранения при /sync.

    /sync берёт лучшую пробежку за 90 дней. Пока VDOT не пересматривался, это и есть оценка формы.
    После пересмотра /sync не поднимает VDOT выше пересмотренного: иначе старая пробежка до перерыва
    вернула бы завышенные темпы. Рост формы подтвердит следующий пересмотр.
    """
    if sync_vdot is None:
        return current if reviewed_on else None
    if reviewed_on is None or current is None:
        return sync_vdot
    return min(sync_vdot, current)

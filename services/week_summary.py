# services/week_summary.py
"""Итог недели для воскресного сообщения: план против факта, лучшая пробежка, тренд пульса на лёгких.
Чистые функции, без I/O.

Данные те же, что для корректировки недели (services.week_adaptation): пробежки Garmin за две недели
и план текущей недели из БД. Все числа считает код, модель итог не видит.
"""
import html
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional, Sequence

from services.activity_zones import classify_activity
from services.coach_service import MAX_VDOT, MIN_VDOT, calculate_vdot, format_pace
from services.heart_rate import HrRef
from services.week_adaptation import RunFact, WeekReview

BEST_RUN_MIN_KM = 3.0           # короче оценка усилия по VDOT ненадёжна (как в find_best_effort)
HR_TREND_MIN = 3                # разница пульса меньше этой считается шумом
PACE_COMPARABLE_SEC = 10        # пульс сравниваем, только если темп лёгких отличается не больше
DAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


@dataclass(frozen=True)
class EasyHr:
    """Средний пульс и темп лёгких пробежек недели (взвешены по дистанции)."""
    hr: float
    pace_sec: float
    runs: int


@dataclass(frozen=True)
class WeekSummary:
    week_start: date
    runs: int
    done_km: float
    minutes: Optional[int]                 # время бега; None, если у пробежек нет темпа
    review: Optional[WeekReview]           # сравнение с планом; None без плана
    best: Optional[RunFact]                # самое сильное усилие по VDOT (от 3 км)
    longest: Optional[RunFact]
    easy_now: Optional[EasyHr]
    easy_before: Optional[EasyHr]


def _minutes(run: RunFact) -> Optional[float]:
    return run.pace_sec * run.distance_km / 60 if run.pace_sec else None


def _effort_vdot(run: RunFact) -> Optional[float]:
    if run.distance_km < BEST_RUN_MIN_KM or not run.pace_sec:
        return None
    vdot = calculate_vdot(run.distance_km * 1000, run.pace_sec * run.distance_km)
    return vdot if MIN_VDOT <= vdot <= MAX_VDOT else None    # артефакты GPS и паузы


def easy_hr(runs: Sequence[RunFact], zones, hr_basis: HrRef) -> Optional[EasyHr]:
    """Лёгкие по темпу пробежки с пульсом. Без зон лёгкость не определить: None."""
    if zones is None:
        return None
    easy = [
        r for r in runs
        if r.avg_hr and r.pace_sec and classify_activity(r.pace_sec, r.avg_hr, zones, hr_basis).pace_zone
        in ("below_E", "E")
    ]
    km = sum(r.distance_km for r in easy)
    if not easy or km <= 0:
        return None
    return EasyHr(
        hr=sum(r.avg_hr * r.distance_km for r in easy) / km,
        pace_sec=sum(r.pace_sec * r.distance_km for r in easy) / km,
        runs=len(easy),
    )


def summarize_week(
    week_start: date,
    runs: Sequence[RunFact],
    today: date,
    review: Optional[WeekReview] = None,
    zones=None,
    hr_basis: HrRef = None,
) -> WeekSummary:
    """Итог недели, начатой week_start (пробежки до today), и для тренда пульса прошлой недели."""
    week_end = min(today, week_start + timedelta(days=6))
    now = [r for r in runs if week_start <= r.day <= week_end]
    before = [r for r in runs if week_start - timedelta(days=7) <= r.day < week_start]
    minutes = [_minutes(r) for r in now]
    efforts = [(v, r) for r in now if (v := _effort_vdot(r)) is not None]
    return WeekSummary(
        week_start=week_start,
        runs=len(now),
        done_km=round(sum(r.distance_km for r in now), 1),
        minutes=round(sum(m for m in minutes if m)) if any(minutes) else None,
        review=review if review is not None and review.compliance is not None else None,
        best=max(efforts, key=lambda e: e[0])[1] if efforts else None,
        longest=max(now, key=lambda r: r.distance_km) if now else None,
        easy_now=easy_hr(now, zones, hr_basis),
        easy_before=easy_hr(before, zones, hr_basis),
    )


def _duration(minutes: int) -> str:
    hours, mins = divmod(minutes, 60)
    return f"{hours} ч {mins} мин" if hours else f"{mins} мин"


def _run_text(run: RunFact) -> str:
    pace = f" в темпе {format_pace(run.pace_sec)} /км" if run.pace_sec else ""
    return f"{DAY_NAMES[run.day.weekday()]}, {run.distance_km:g} км{pace}"


def hr_trend_text(now: Optional[EasyHr], before: Optional[EasyHr]) -> Optional[str]:
    """Тренд пульса на лёгких: сравнение с прошлой неделей при сопоставимом темпе (обычный текст)."""
    if now is None:
        return None
    line = f"{round(now.hr)} уд/мин при {format_pace(now.pace_sec)} /км ({now.runs} проб.)"
    if before is None:
        return line
    d_hr = round(now.hr - before.hr)
    d_pace = now.pace_sec - before.pace_sec          # больше нуля: медленнее
    line += f", неделей раньше {round(before.hr)} при {format_pace(before.pace_sec)} /км"
    if d_hr <= -HR_TREND_MIN and d_pace <= PACE_COMPARABLE_SEC:
        return f"{line}: пульс ниже на {-d_hr} при том же или более быстром темпе — форма растёт"
    if d_hr >= HR_TREND_MIN and d_pace >= -PACE_COMPARABLE_SEC:
        return (
            f"{line}: пульс выше на {d_hr} при том же или более медленном темпе — "
            "возможны усталость, жара или начало болезни"
        )
    if abs(d_pace) <= PACE_COMPARABLE_SEC:
        return f"{line}: без заметных изменений"
    return line


def render_week_summary(summary: WeekSummary) -> str:
    """Сообщение «Итог недели» (HTML, все числа от кода)."""
    end = summary.week_start + timedelta(days=6)
    lines = [f"📋 <b>Итог недели</b> {summary.week_start:%d.%m}–{end:%d.%m}"]
    if summary.runs == 0:
        lines.append("Пробежек в Garmin за неделю нет.")
    review = summary.review
    if review is not None:
        lines.append(
            f"• Выполнено: <b>{review.done_km:g} из {review.planned_km:g} км</b> ({review.compliance:.0%}), "
            f"качественных {review.done_quality} из {review.planned_quality}"
        )
    elif summary.runs:
        lines.append(f"• Пробежано: <b>{summary.done_km:g} км</b>")
    if summary.runs:
        time = f", {_duration(summary.minutes)}" if summary.minutes else ""
        lines.append(f"• Пробежек: {summary.runs}{time}")
    if summary.best is not None:
        lines.append(f"• Лучшая по усилию: {_run_text(summary.best)}")
    if summary.longest is not None and summary.longest is not summary.best:
        lines.append(f"• Самая длинная: {_run_text(summary.longest)}")
    trend = hr_trend_text(summary.easy_now, summary.easy_before)
    if trend:
        lines.append(f"• Пульс на лёгких: {html.escape(trend)}")
    return "\n".join(lines)


def has_content(summary: WeekSummary) -> bool:
    """Отправлять ли итог: есть пробежки или план, с которым можно сравнить."""
    return summary.runs > 0 or summary.review is not None


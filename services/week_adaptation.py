# services/week_adaptation.py
"""Корректировка следующей недели по факту прошлой и по восстановлению. Чистые функции, без I/O.

Код сравнивает план прошлой недели с пробежками из Garmin, смотрит на признаки усталости
(статус HRV, готовность к тренировкам, пульс выше зоны на лёгких пробежках) и выдаёт поправку:
целевой километраж и предел качественных тренировок. Модель получает готовые числа.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

from schemas.plan import QUALITY_TYPES, RUNNING_TYPES, WeekPlan
from services.activity_zones import classify_activity
from services.coach_service import TrainingZones
from services.plan_validator import MAX_WEEKLY_GROWTH

MIN_DAYS_FOR_REVIEW = 4          # раньше четверга выполнение недели не оцениваем: мало данных
BREAK_COMPLIANCE = 0.3           # меньше 30% плана: перерыв (болезнь, травма)
LOW_COMPLIANCE = 0.7             # меньше 70%: неделя недовыполнена
HIGH_COMPLIANCE = 1.2            # больше 120%: перевыполнение
RESTART_SHARE = 0.6              # после перерыва неделя не ниже 60% плановой: с нуля не начинаем
FATIGUE_SHARE = 0.8              # при усталости неделя не больше 80% плановой
READINESS_LOW = 25               # готовность Garmin ниже: сильный сигнал усталости
READINESS_MODERATE = 50          # ниже: слабый сигнал
EASY_HR_HIGH_RUNS = 2            # столько лёгких пробежек с пульсом выше зоны: слабый сигнал
QUALITY_ANAEROBIC_TE = 2.0       # анаэробный эффект Garmin от 2.0: тренировка была качественной
KM_ROUND = 0.5

HRV_STRONG = {"LOW", "POOR"}
HRV_WEAK = {"UNBALANCED"}


@dataclass(frozen=True)
class RunFact:
    day: date
    distance_km: float
    pace_sec: Optional[float] = None
    avg_hr: Optional[float] = None
    anaerobic_te: Optional[float] = None


@dataclass(frozen=True)
class WeekReview:
    """Факт прошлой недели против плана. compliance None: плана нет или прошло слишком мало дней."""
    planned_km: float
    done_km: float
    planned_quality: int
    done_quality: int
    easy_hr_high: int            # лёгкие по темпу пробежки с пульсом выше зоны темпа
    days_covered: int

    @property
    def compliance(self) -> Optional[float]:
        if self.planned_km <= 0 or self.days_covered < MIN_DAYS_FOR_REVIEW:
            return None
        return self.done_km / self.planned_km


@dataclass(frozen=True)
class RecoverySignals:
    hrv_status: Optional[str] = None
    readiness: Optional[int] = None


@dataclass(frozen=True)
class WeekAdjustment:
    target_km: float
    max_quality: Optional[int] = None                 # None: обычные правила недели
    reasons: List[str] = field(default_factory=list)  # для сообщения и промпта, числа от кода
    summary: Optional[str] = None                     # «Прошлая неделя: 28 из 40 км (70%)…»

    @property
    def changed(self) -> bool:
        return bool(self.reasons)


def run_facts(runs: Iterable[Dict[str, Any]]) -> List[RunFact]:
    """Пробежки Garmin (формат parse_last_activity) -> RunFact. Без даты или дистанции пропускаются."""
    facts = []
    for run in runs:
        if not run.get("is_running", True) or not run.get("distance_km"):
            continue
        try:
            day = datetime.fromisoformat(str(run.get("start_time")).replace("Z", "")).date()
        except ValueError:
            continue
        facts.append(RunFact(
            day=day,
            distance_km=float(run["distance_km"]),
            pace_sec=run.get("avg_pace_sec"),
            avg_hr=run.get("avg_heart_rate"),
            anaerobic_te=run.get("anaerobic_te"),
        ))
    return facts


def _is_quality(run: RunFact, zones: Optional[TrainingZones], max_hr: Optional[int]) -> bool:
    """Средний темп интервальной тренировки размыт разминкой и отдыхом, поэтому смотрим и на эффект Garmin."""
    if run.anaerobic_te is not None and run.anaerobic_te >= QUALITY_ANAEROBIC_TE:
        return True
    return classify_activity(run.pace_sec, run.avg_hr, zones, max_hr).pace_zone in ("M", "T", "I", "R")


def review_week(
    planned: Optional[WeekPlan],
    week_start: date,
    runs: List[RunFact],
    today: date,
    zones: Optional[TrainingZones] = None,
    max_hr: Optional[int] = None,
) -> WeekReview:
    """Сравнение недели, начатой week_start, с пробежками до today включительно.

    Сегодняшний плановый день учитывается, только если сегодня уже была пробежка: в воскресенье
    в 15:00 длительный мог ещё не состояться, и это не недовыполнение.
    """
    week_end = week_start + timedelta(days=6)
    week_runs = [r for r in runs if week_start <= r.day <= min(today, week_end)]
    ran_today = any(r.day == today for r in week_runs)

    planned_km, planned_quality = 0.0, 0
    for day in planned.days if planned else []:
        when = week_start + timedelta(days=day.day - 1)
        if when > today or (when == today and not ran_today) or day.type not in RUNNING_TYPES:
            continue
        planned_km += day.distance_km or 0.0
        planned_quality += day.type in QUALITY_TYPES

    easy_hr_high = 0
    for r in week_runs:
        z = classify_activity(r.pace_sec, r.avg_hr, zones, max_hr)
        if z.pace_zone in ("below_E", "E") and z.hr_above_pace:
            easy_hr_high += 1

    return WeekReview(
        planned_km=round(planned_km, 1),
        done_km=round(sum(r.distance_km for r in week_runs), 1),
        planned_quality=planned_quality,
        done_quality=sum(_is_quality(r, zones, max_hr) for r in week_runs),
        easy_hr_high=easy_hr_high,
        days_covered=min(7, (today - week_start).days + 1),
    )


def fatigue_reasons(review: Optional[WeekReview], signals: RecoverySignals) -> List[str]:
    """Признаки усталости. Сильный признак достаточен один, слабых нужно два."""
    strong, weak = [], []
    if signals.hrv_status in HRV_STRONG:
        strong.append("вариабельность пульса (HRV) ниже обычной")
    elif signals.hrv_status in HRV_WEAK:
        weak.append("вариабельность пульса (HRV) нестабильна")
    if signals.readiness is not None:
        if signals.readiness < READINESS_LOW:
            strong.append(f"готовность к тренировкам {signals.readiness} из 100")
        elif signals.readiness < READINESS_MODERATE:
            weak.append(f"готовность к тренировкам {signals.readiness} из 100")
    if review and review.easy_hr_high >= EASY_HR_HIGH_RUNS:
        weak.append(f"пульс выше зоны на {review.easy_hr_high} лёгких пробежках")
    return strong + weak if strong or len(weak) >= 2 else []


def _round_km(km: float) -> float:
    return round(km / KM_ROUND) * KM_ROUND


def adjust_next_week(
    plan_target_km: float,
    review: Optional[WeekReview],
    signals: Optional[RecoverySignals] = None,
) -> WeekAdjustment:
    """Целевой километраж и предел качественных тренировок следующей недели.

    Объём не поднимается выше плана макроцикла: поправка только снижает нагрузку.
    """
    signals = signals or RecoverySignals()
    target = plan_target_km
    max_quality: Optional[int] = None
    reasons: List[str] = []
    summary = None

    def limit_quality(n: int) -> None:
        nonlocal max_quality
        max_quality = n if max_quality is None else min(max_quality, n)

    compliance = review.compliance if review else None
    if review and compliance is not None:
        summary = (
            f"Прошлая неделя: {review.done_km:g} из {review.planned_km:g} км ({compliance:.0%}), "
            f"качественных {review.done_quality} из {review.planned_quality}"
        )
        restart = max(review.done_km * (1 + MAX_WEEKLY_GROWTH), plan_target_km * RESTART_SHARE)
        if compliance < BREAK_COMPLIANCE:
            target = min(target, restart)
            limit_quality(0)
            reasons.append("почти без бега на прошлой неделе: возвращаемся постепенно, без качественных тренировок")
        elif compliance < LOW_COMPLIANCE:
            target = min(target, restart)
            limit_quality(1)
            reasons.append(
                f"прошлая неделя выполнена меньше чем на {LOW_COMPLIANCE:.0%}: объём растёт не больше "
                f"{MAX_WEEKLY_GROWTH:.0%} от фактического"
            )
        elif compliance > HIGH_COMPLIANCE:
            reasons.append("прошлая неделя перевыполнена: объём выше плана не поднимаем, лёгкие дни держите лёгкими")

        missed = review.planned_quality - review.done_quality
        if missed >= 2:
            limit_quality(1)
            reasons.append(f"пропущено качественных тренировок: {missed}; не догоняем, одна качественная за неделю")
        elif missed == 1 and compliance >= BREAK_COMPLIANCE:
            reasons.append("пропущенная качественная тренировка не переносится")

    fatigue = fatigue_reasons(review, signals)
    if fatigue:
        target = min(target, plan_target_km * FATIGUE_SHARE)
        limit_quality(1)
        reasons.append("признаки усталости (" + ", ".join(fatigue) + "): облегчённая неделя")

    return WeekAdjustment(
        target_km=_round_km(target) if target < plan_target_km else plan_target_km,
        max_quality=max_quality,
        reasons=reasons,
        summary=summary,
    )


def review_to_dict(review: WeekReview) -> Dict[str, Any]:
    """Факт недели для weekly_plans.review: по нему следующая рассылка видит «две слабые недели подряд»."""
    return {
        "planned_km": review.planned_km,
        "done_km": review.done_km,
        "compliance": round(review.compliance, 3) if review.compliance is not None else None,
    }

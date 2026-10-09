# services/garmin_workouts.py
"""Тренировки для календаря Garmin из дней плана. Чистые функции, без I/O.

Структуру тренировки (разминка, рабочая часть, повторы, заминка), целевой темп и пульс считает код.
Тренировка из каталога (workout_id) строится по шаблону services.workout_catalog. В старых планах повторы
берутся из описания дня ("5 × 1000 м, отдых 2 мин"), только если их объём сходится с quality_km;
иначе рабочая часть выгружается одним отрезком.
"""
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from garminconnect.workout import (
    ConditionType,
    CustomHeartRateTarget,
    PaceTarget,
    RunningWorkout,
    WorkoutSegment,
    create_cooldown_step,
    create_distance_interval_step,
    create_interval_step,
    create_recovery_step,
    create_repeat_group,
    create_targeted_distance_interval_step,
    create_targeted_interval_step,
    create_warmup_step,
)

from schemas.plan import RUNNING_TYPES, PlannedDay, WeekPlan
from services.coach_service import PaceRange, TrainingZones
from services.heart_rate import HrRef
from services.plan_paces import estimate_duration_min, hr_range_for_zone, pace_for_zone
from services.plan_renderer import TYPE_LABELS
from services.workout_catalog import Repeat, Step, workout_steps

WORKOUT_PREFIX = "AI Coach"
NAME_MAX_LEN = 80
DESCRIPTION_MAX_LEN = 500

PACE_TOLERANCE_SEC = 5        # у зон M/T/I/R один темп: коридор на часах ±5 с/км
REPEATS_TOLERANCE = 0.25      # повторы из описания принимаются, если их объём в пределах ±25% от quality_km
MIN_STEP_KM = 0.2             # разминка или заминка короче 200 м не выгружается
RECOVERY_ROUND_SEC = 15

# Восстановление между отрезками по Дэниелсу, доля от времени отрезка, и границы в секундах
RECOVERY_RULES: Dict[str, Tuple[float, int, int]] = {
    "T": (0.2, 60, 180),      # 1 мин на каждые 5 мин работы
    "M": (0.2, 60, 180),
    "I": (1.0, 60, 300),      # трусцой примерно столько же, сколько длился отрезок
    "R": (2.0, 60, 400),      # трусцой примерно ту же дистанцию, это вдвое дольше
}

_REPEATS_RE = re.compile(r"(\d{1,2})\s*[×xх*]\s*(\d+(?:[.,]\d+)?)\s*(км|km|м|m)\b", re.IGNORECASE)
_RECOVERY_RE = re.compile(
    r"(?:отдых|восстановлени\w*|трусц\w*|пауз\w*)\D{0,15}?(\d+(?:[.,]\d+)?)\s*(мин|сек|с)\b", re.IGNORECASE
)


@dataclass(frozen=True)
class Repeats:
    count: int
    distance_m: float
    recovery_sec: Optional[float] = None   # None: восстановление считает код по зоне


def _num(text: str) -> float:
    return float(text.replace(",", "."))


def parse_repeats(description: str, quality_km: Optional[float]) -> Optional[Repeats]:
    """Повторы из описания дня. None, если их нет или объём не сходится с рабочей частью."""
    match = _REPEATS_RE.search(description or "")
    if not match or not quality_km:
        return None
    count = int(match.group(1))
    distance_m = _num(match.group(2)) * (1000 if match.group(3).lower() in ("км", "km") else 1)
    if count < 2 or distance_m < 100:
        return None
    if abs(count * distance_m / 1000 - quality_km) > quality_km * REPEATS_TOLERANCE:
        return None

    recovery_sec = None
    rec = _RECOVERY_RE.search(description[match.end():])
    if rec:
        value = _num(rec.group(1))
        recovery_sec = value * 60 if rec.group(2).lower() == "мин" else value
    return Repeats(count, distance_m, recovery_sec)


def recovery_seconds(zone: Optional[str], rep_m: float, zones: Optional[TrainingZones]) -> float:
    """Восстановление между отрезками по правилам Дэниелса, кратное 15 с."""
    share, low, high = RECOVERY_RULES.get(zone or "", (1.0, 60, 300))
    pace = pace_for_zone(zone, zones)
    rep_sec = rep_m / 1000 * (pace.fast + pace.slow) / 2 if pace else 0
    seconds = min(high, max(low, rep_sec * share))
    return round(seconds / RECOVERY_ROUND_SEC) * RECOVERY_ROUND_SEC


def pace_target_mps(pace: PaceRange) -> Tuple[float, float]:
    """Коридор темпа в м/с (нижняя граница медленнее). Для одиночного темпа ±PACE_TOLERANCE_SEC."""
    fast, slow = pace.fast, pace.slow
    if slow - fast < 2 * PACE_TOLERANCE_SEC:
        mid = (fast + slow) / 2
        fast, slow = mid - PACE_TOLERANCE_SEC, mid + PACE_TOLERANCE_SEC
    return round(1000 / slow, 3), round(1000 / fast, 3)


def step_target(zone: Optional[str], zones: Optional[TrainingZones], hr_basis: HrRef) -> Optional[Any]:
    """Цель шага: темп по VDOT, без зон пульс по ЧССmax или ПАНО, иначе без цели."""
    pace = pace_for_zone(zone, zones)
    if pace:
        low, high = pace_target_mps(pace)
        return PaceTarget(lower_limit=low, upper_limit=high)
    hr = hr_range_for_zone(zone, hr_basis)
    if hr:
        return CustomHeartRateTarget(lower_limit=hr[0], upper_limit=hr[1])
    return None


def _distance_step(km: float, order: int, zone: Optional[str], zones, hr_basis) -> Any:
    meters = float(round(km * 1000))
    target = step_target(zone, zones, hr_basis)
    if target is None:
        return create_distance_interval_step(meters, order)
    return create_targeted_distance_interval_step(meters, order, target)


def _as_distance(step: Any, km: float) -> Any:
    """Разминка и заминка в библиотеке заданы по времени: переводим их на дистанцию."""
    step.endCondition = {"conditionTypeId": ConditionType.DISTANCE, "conditionTypeKey": "distance",
                         "displayOrder": 3, "displayable": True}
    step.endConditionValue = float(round(km * 1000))
    return step


def _time_step(seconds: float, order: int, zone: Optional[str], zones, hr_basis) -> Any:
    target = step_target(zone, zones, hr_basis)
    if target is None:
        return create_interval_step(float(seconds), order)
    return create_targeted_interval_step(float(seconds), order, target)


def _catalog_step(step: Step, order: int, zones, hr_basis) -> Any:
    """Шаг каталога -> шаг библиотеки. Разминка, заминка и восстановление без цели, работа с целью зоны."""
    if step.kind == "work":
        zone = step.zone if step.target else None   # в гору цель по темпу бессмысленна
        if step.distance_m:
            return _distance_step(step.distance_m / 1000, order, zone, zones, hr_basis)
        return _time_step(step.duration_s, order, zone, zones, hr_basis)
    factory = {"warmup": create_warmup_step, "cooldown": create_cooldown_step,
               "recovery": create_recovery_step}[step.kind]
    if step.distance_m:
        return _as_distance(factory(0, order), step.distance_m / 1000)
    return factory(float(step.duration_s), order)


def _catalog_steps(specs: List[object], zones, hr_basis) -> List[Any]:
    steps: List[Any] = []
    order = 1
    for spec in specs:
        if isinstance(spec, Repeat):
            children = [_catalog_step(child, order + 1 + i, zones, hr_basis) for i, child in enumerate(spec.steps)]
            steps.append(create_repeat_group(spec.iterations, children, order))
            order += 1 + len(children)
        else:
            steps.append(_catalog_step(spec, order, zones, hr_basis))
            order += 1
    return steps


def build_steps(day: PlannedDay, zones: Optional[TrainingZones], hr_basis: HrRef) -> List[Any]:
    """Шаги тренировки. Лёгкий и длительный бег одним отрезком в зоне E, качественная с разминкой и заминкой.
    Тренировка из каталога строится по своему шаблону, старые дни без workout_id разбираются по описанию."""
    specs = workout_steps(day, zones)
    if specs:
        return _catalog_steps(specs, zones, hr_basis)
    distance = day.distance_km or 0.0
    if not day.quality_km:
        return [_distance_step(distance, 1, day.zone, zones, hr_basis)]

    repeats = parse_repeats(day.description, day.quality_km)
    work_km = day.quality_km
    recovery_km = 0.0
    if repeats:
        rec_sec = repeats.recovery_sec or recovery_seconds(day.zone, repeats.distance_m, zones)
        work_km = repeats.count * repeats.distance_m / 1000
        if zones is not None:   # восстановление трусцой в медленной части лёгкого диапазона
            recovery_km = repeats.count * rec_sec / zones.easy.slow

    side_km = max(0.0, distance - work_km - recovery_km) / 2
    side_km = round(side_km, 1)
    steps: List[Any] = []
    order = 1
    if side_km >= MIN_STEP_KM:
        steps.append(_as_distance(create_warmup_step(0, order), side_km))
        order += 1

    if repeats:
        rep = _distance_step(repeats.distance_m / 1000, order + 1, day.zone, zones, hr_basis)
        rest = create_recovery_step(float(rec_sec), order + 2)
        steps.append(create_repeat_group(repeats.count, [rep, rest], order))
        order += 3
    else:
        steps.append(_distance_step(work_km, order, day.zone, zones, hr_basis))
        order += 1

    if side_km >= MIN_STEP_KM:
        steps.append(_as_distance(create_cooldown_step(0, order), side_km))
    return steps


def workout_name(day: PlannedDay) -> str:
    return f"{WORKOUT_PREFIX} · {TYPE_LABELS[day.type]} {day.distance_km:g} км"[:NAME_MAX_LEN]


def build_workout(
    day: PlannedDay,
    zones: Optional[TrainingZones],
    hr_basis: HrRef,
) -> Optional[Dict[str, Any]]:
    """JSON тренировки для workout-service Garmin. None для дня без бега (отдых, ОФП)."""
    if day.type not in RUNNING_TYPES or not day.distance_km:
        return None
    duration = estimate_duration_min(day, zones)
    label = TYPE_LABELS[day.type]
    workout = RunningWorkout(
        workoutName=workout_name(day),
        estimatedDurationInSecs=(duration or 0) * 60,
        description=(day.description.strip() or label)[:DESCRIPTION_MAX_LEN],
        workoutSegments=[WorkoutSegment(
            segmentOrder=1,
            sportType={"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1},
            workoutSteps=build_steps(day, zones, hr_basis),
        )],
    )
    return workout.to_dict()


def export_days(week: WeekPlan, week_start: date, week_end: date, today: date) -> List[Tuple[date, PlannedDay]]:
    """Беговые дни недели с датами: только с сегодняшнего дня и в границах сохранённой недели."""
    result = []
    for day in week.days:
        when = week_start + timedelta(days=day.day - 1)
        if when < today or when > week_end or day.type not in RUNNING_TYPES:
            continue
        result.append((when, day))
    return result


def split_previous(previous: Optional[List[Dict[str, Any]]], today: date) -> Tuple[List[int], List[Dict[str, Any]]]:
    """Ранее выгруженные тренировки недели: (id на замену, записи о прошедших днях, которые остаются).

    Прошедшие дни не трогаем: тренировка уже выполнена или пропущена, её запись в календаре остаётся.
    """
    replace, keep = [], []
    for item in previous or []:
        try:
            when = date.fromisoformat(item["date"])
            workout_id = int(item["workout_id"])
        except (KeyError, TypeError, ValueError):
            continue
        if when < today:
            keep.append(item)
        else:
            replace.append(workout_id)
    return replace, keep

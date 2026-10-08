# services/plan_validator.py
"""Проверка планов по правилам Дэниелса и Фицджеральда. Чистые функции, без I/O.

Каждая функция возвращает список замечаний (пустой список = план корректен).
"""
from typing import Dict, FrozenSet, List, Optional

from schemas.plan import QUALITY_TYPES, MacroPlan, WeekPlan, WorkoutType

DAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

# Дни, после которых нужен лёгкий день (принцип hard-easy)
HARD_TYPES = QUALITY_TYPES | {WorkoutType.LONG}

# Лимиты рабочей части: (доля недельного километража, абсолютный потолок в км)
QUALITY_LIMITS = {
    WorkoutType.THRESHOLD: (0.10, 15.0),
    WorkoutType.INTERVAL: (0.08, 10.0),
    WorkoutType.REPETITION: (0.05, 8.0),
}
LONG_MAX_SHARE = 0.30
LONG_MAX_MINUTES = 150
MAX_QUALITY_SESSIONS = 3
MIN_REST_DAYS = 1                 # отдых или ОФП
TARGET_KM_TOLERANCE = 0.15        # допустимое отклонение от целевого километража недели
MAX_WEEKLY_GROWTH = 0.10          # рост относительно лучшей из двух предыдущих недель
TAPER_MAX_SHARE = 0.75            # последняя неделя макроплана не больше этой доли от пиковой
KM_EPS = 0.05

# Типы тренировок, запрещённые в фазе. Фаза I (закладка фундамента) по Дэниелсу: только лёгкий бег,
# длительный и ускорения; ускорения описываются внутри easy, качественных тренировок нет.
PHASE_FORBIDDEN_TYPES: Dict[int, FrozenSet[WorkoutType]] = {
    1: QUALITY_TYPES,
}


def validate_week(
    week: WeekPlan, target_km: Optional[float] = None, phase_number: Optional[int] = None,
) -> List[str]:
    """Проверяет недельный микроцикл.

    target_km: плановый километраж недели из макроплана; phase_number: номер фазы (1-4) для правил фазы.
    """
    problems: List[str] = []
    total = week.total_km
    if total <= 0:
        return ["в неделе нет беговых тренировок"]

    # 1. Принцип hard-easy: два тяжёлых дня подряд недопустимы
    for current, nxt in zip(week.days, week.days[1:]):
        if current.type in HARD_TYPES and nxt.type in HARD_TYPES:
            problems.append(
                f"{DAY_NAMES[current.day - 1]} и {DAY_NAMES[nxt.day - 1]}: две тяжёлые тренировки подряд "
                f"({current.type.value}, {nxt.type.value}); между ними нужен лёгкий день или отдых"
            )

    # 2. Лимиты объёма рабочей части
    for d in week.days:
        limit = QUALITY_LIMITS.get(d.type)
        if limit and d.quality_km:
            share, absolute = limit
            cap = min(share * total, absolute)
            if d.quality_km > cap + KM_EPS:
                problems.append(
                    f"{DAY_NAMES[d.day - 1]}: рабочая часть '{d.type.value}' {d.quality_km:g} км превышает лимит "
                    f"{cap:.1f} км ({share:.0%} от недели {total:g} км, не более {absolute:g} км)"
                )

    # 3. Длительный бег
    for d in week.days:
        if d.type != WorkoutType.LONG:
            continue
        if (d.distance_km or 0) > LONG_MAX_SHARE * total + KM_EPS:
            problems.append(
                f"{DAY_NAMES[d.day - 1]}: длительный бег {d.distance_km:g} км больше {LONG_MAX_SHARE:.0%} "
                f"недельного километража ({total:g} км)"
            )
        if (d.duration_min or 0) > LONG_MAX_MINUTES:
            problems.append(
                f"{DAY_NAMES[d.day - 1]}: длительный бег {d.duration_min} мин дольше потолка {LONG_MAX_MINUTES} мин"
            )
        longer = [o for o in week.days if o.type != WorkoutType.LONG and (o.distance_km or 0) > d.distance_km + KM_EPS]
        if longer:
            other = max(longer, key=lambda o: o.distance_km)
            problems.append(
                f"{DAY_NAMES[d.day - 1]}: длительный бег {d.distance_km:g} км короче тренировки "
                f"{DAY_NAMES[other.day - 1]} ({other.type.value}, {other.distance_km:g} км); "
                "длительный должен быть самой длинной пробежкой недели"
            )

    # 4. Количество качественных тренировок и дни отдыха
    quality_count = sum(1 for d in week.days if d.type in QUALITY_TYPES)
    if quality_count > MAX_QUALITY_SESSIONS:
        problems.append(
            f"слишком много качественных тренировок: {quality_count} (максимум {MAX_QUALITY_SESSIONS})"
        )
    rest_days = sum(1 for d in week.days if d.type in (WorkoutType.REST, WorkoutType.CROSS))
    if rest_days < MIN_REST_DAYS:
        problems.append("нет ни одного дня отдыха или ОФП")

    # 5. Типы тренировок, запрещённые в текущей фазе
    forbidden = PHASE_FORBIDDEN_TYPES.get(phase_number, frozenset())
    for d in week.days:
        if d.type in forbidden:
            problems.append(
                f"{DAY_NAMES[d.day - 1]}: тренировка '{d.type.value}' недопустима в фазе {phase_number}; "
                f"запрещены: {_type_list(forbidden)}"
            )

    # 6. Соответствие целевому километражу недели
    if target_km:
        if abs(total - target_km) > TARGET_KM_TOLERANCE * target_km:
            problems.append(
                f"километраж недели {total:g} км отклоняется от планового {target_km:g} км "
                f"более чем на {TARGET_KM_TOLERANCE:.0%}"
            )

    return problems


def validate_macro(macro: MacroPlan, total_weeks: int) -> List[str]:
    """Проверяет макроцикл относительно заданной длительности подготовки."""
    problems: List[str] = []

    if macro.total_weeks != total_weeks:
        problems.append(
            f"сумма недель по фазам {macro.total_weeks}, а на подготовку отведено {total_weeks}"
        )

    km = macro.weekly_km
    for i in range(1, len(km)):
        reference = max(km[max(0, i - 2):i])
        limit = reference * (1 + MAX_WEEKLY_GROWTH)
        if km[i] > limit + KM_EPS:
            problems.append(
                f"неделя {i + 1}: {km[i]:g} км, рост выше {MAX_WEEKLY_GROWTH:.0%} "
                f"относительно предыдущих недель (максимум {limit:.1f} км)"
            )

    peak = max(km)
    if len(km) >= 2 and km[-1] > TAPER_MAX_SHARE * peak + KM_EPS:
        problems.append(
            f"последняя неделя {km[-1]:g} км: перед стартом объём должен снижаться до "
            f"{TAPER_MAX_SHARE:.0%} от пика ({peak:g} км) или ниже, то есть не более {TAPER_MAX_SHARE * peak:.1f} км"
        )

    return problems


def _type_list(types: FrozenSet[WorkoutType]) -> str:
    return ", ".join(sorted(t.value for t in types))


def format_problems(problems: List[str]) -> str:
    """Список замечаний в виде текста для повторного запроса к модели."""
    return "\n".join(f"- {p}" for p in problems)
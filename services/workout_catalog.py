# services/workout_catalog.py
"""Каталог качественных тренировок из книг. Чистые функции, без I/O.

Модель выбирает для качественного дня тренировку из каталога (workout_id) и число повторов (reps),
а объём рабочей части, описание дня, проверку и шаги для часов Garmin считает код. Тренировки взяты
из таблиц 4.2–4.4 и главы 10 «От 800 метров до марафона» Дэниелса (уровни по недельному километражу,
фазы: II повторы, III интервалы, IV порог) и из главы 7 «Бег по правилу 80/20» Фицджеральда
(быстрый финиш, повторы в гору). Повторы ограничены теми же лимитами, что в plan_validator.
"""
import math
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Tuple

from schemas.plan import PlannedDay, WeekPlan, WorkoutType
from services.coach_service import TrainingZones
from services.plan_paces import pace_for_zone
from services.plan_validator import QUALITY_LIMITS

# Лимит рабочей части в М-темпе за тренировку (Дэниелс, гл. 4): 20% недели и не больше 29 км.
# В plan_validator его нет: марафонский темп там проверяется общими правилами недели
MARATHON_LIMIT = (0.20, 29.0)
LIMITS: Dict[WorkoutType, Tuple[float, float]] = {**QUALITY_LIMITS, WorkoutType.MARATHON: MARATHON_LIMIT}
TYPE_BY_ZONE = {
    "M": WorkoutType.MARATHON, "T": WorkoutType.THRESHOLD, "I": WorkoutType.INTERVAL, "R": WorkoutType.REPETITION,
}

WARMUP_MIN_KM = 1.5        # Дэниелс: 10 мин в Л-темпе и ускорения перед качественной работой
COOLDOWN_MIN_KM = 1.0
FINISH_EASY_MIN_KM = 3.0   # быстрый финиш: перед ним не меньше 3 км лёгкого бега
DEFAULT_JOG_SEC_PER_KM = 420   # трусца без VDOT: 7:00 /км, только для оценки дистанции восстановления
MARATHON_MIN_RACE_M = 21000    # М-темп только при подготовке к полумарафону и длиннее
KM_EPS = 0.05
DAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


@dataclass(frozen=True)
class Block:
    """Часть тренировки: reps отрезков (distance_m или duration_s) в зоне, между ними восстановление.

    continuous: отрезки бегутся слитно, без восстановления (20 мин П как 4 × 5 мин подряд).
    lead_jog_s: трусца перед блоком (между блоками смешанной тренировки).
    """
    zone: str
    reps: int = 1
    distance_m: Optional[int] = None
    duration_s: Optional[int] = None
    recovery_s: Optional[int] = None
    recovery_m: Optional[int] = None
    continuous: bool = False
    lead_jog_s: int = 0


@dataclass(frozen=True)
class Template:
    id: str
    name: str
    type: WorkoutType
    blocks: Tuple[Block, ...]
    phases: FrozenSet[int]
    source: str
    # Число повторов первого блока выбирает модель в этих пределах; None: тренировка фиксированная
    reps_range: Optional[Tuple[int, int]] = None
    min_week_km: float = 0.0
    finish: bool = False   # рабочая часть в конце пробежки, без заминки
    hill: bool = False     # в гору: цель по темпу на часах не ставится
    note: str = ""


def _t(tid: str, name: str, block: Block, reps: Optional[Tuple[int, int]], phases, source: str, **kw) -> Template:
    """Шаблон из одного блока: тип тренировки по зоне блока."""
    return Template(tid, name, TYPE_BY_ZONE[block.zone], (block,), frozenset(phases), source, reps, **kw)


D42, D43, D44 = "Дэниелс, табл. 4.2", "Дэниелс, табл. 4.3", "Дэниелс, табл. 4.4"
F7 = "Фицджеральд, «Бег по правилу 80/20», гл. 7"
MIN = 60

CATALOG: Dict[str, Template] = {t.id: t for t in [
    # ---- Порог (П = T): основной тип фазы IV, дополнительный в фазе III ----
    _t("T-tempo-20", "Темповый бег 20 мин", Block("T", 1, duration_s=20 * MIN), None, {3, 4}, f"{D42}, А1"),
    _t("T-cruise-5min", "Крейсерские интервалы 5 мин", Block("T", duration_s=5 * MIN, recovery_s=MIN),
       (3, 8), {3, 4}, f"{D42}, С1"),
    _t("T-cruise-1600", "Крейсерские интервалы 1,6 км", Block("T", distance_m=1600, recovery_s=MIN),
       (3, 6), {3, 4}, "Дэниелс, гл. 4: 5 × 1 миля П / 1 мин"),
    _t("T-cruise-3200", "Крейсерские интервалы 3,2 км", Block("T", distance_m=3200, recovery_s=2 * MIN),
       (2, 3), {3, 4}, "Дэниелс, гл. 4: 3 × 2 мили П / 2 мин", min_week_km=50),
    _t("T-cruise-6min", "Крейсерские интервалы 6 мин", Block("T", duration_s=6 * MIN, recovery_s=MIN),
       (4, 6), {3, 4}, f"{D42}, В1", min_week_km=66),
    _t("T-cruise-12min", "Пороговые отрезки 12 мин", Block("T", duration_s=12 * MIN, recovery_s=2 * MIN),
       (2, 3), {4}, f"{D42}, В3", min_week_km=66),
    _t("T-cruise-15min", "Пороговые отрезки 15 мин", Block("T", duration_s=15 * MIN, recovery_s=3 * MIN),
       (2, 2), {4}, f"{D42}, В4", min_week_km=66),
    Template(
        "T-tempo-20-R200", "Темповый бег 20 мин + повторы 200 м", WorkoutType.THRESHOLD,
        (Block("T", 1, duration_s=20 * MIN), Block("R", 4, distance_m=200, recovery_m=200, lead_jog_s=3 * MIN)),
        frozenset({3, 4}), "Дэниелс, гл. 10: третья и четвёртая фазы", None, min_week_km=40,
    ),
    _t("T-finish", "Быстрый финиш", Block("T", duration_s=5 * MIN, continuous=True), (1, 3), {2, 3, 4},
       f"{F7}, пробежка с быстрым финишем", finish=True,
       note="лёгкий бег, последние минуты в пороговом темпе"),

    # ---- Интервалы (И = I): основной тип фазы III ----
    _t("I-2min", "Интервалы 2 мин", Block("I", duration_s=2 * MIN, recovery_s=MIN), (5, 10), {3, 4},
       f"{D43}, А1, В1, D6"),
    _t("I-3min", "Интервалы 3 мин", Block("I", duration_s=3 * MIN, recovery_s=2 * MIN), (4, 7), {3, 4},
       f"{D43}, А2, В2, С2"),
    _t("I-4min", "Интервалы 4 мин", Block("I", duration_s=4 * MIN, recovery_s=3 * MIN), (3, 6), {3, 4},
       f"{D43}, А3, В3, D4"),
    _t("I-5min", "Интервалы 5 мин", Block("I", duration_s=5 * MIN, recovery_s=4 * MIN), (3, 6), {3, 4},
       f"{D43}, С5, Е3", min_week_km=64),
    _t("I-800", "Интервалы 800 м", Block("I", distance_m=800, recovery_s=2 * MIN), (4, 6), {3, 4},
       f"{D43}, А4, В4, С1"),
    _t("I-1000", "Интервалы 1000 м", Block("I", distance_m=1000, recovery_s=3 * MIN), (4, 8), {3, 4},
       f"{D43}, В5, С3, D1", min_week_km=48),
    _t("I-1200", "Интервалы 1200 м", Block("I", distance_m=1200, recovery_s=3 * MIN), (4, 6), {3, 4},
       f"{D43}, С4, D2", min_week_km=64),
    _t("I-1600", "Интервалы 1,6 км", Block("I", distance_m=1600, recovery_s=4 * MIN), (3, 4), {3, 4},
       f"{D43}, D3", min_week_km=74),

    # ---- Повторы (Пв = R): основной тип фазы II, поддержка скорости в III и IV ----
    _t("R-200", "Повторы 200 м", Block("R", distance_m=200, recovery_m=200), (6, 16), {2, 3, 4},
       f"{D44}, А1, В1"),
    _t("R-300", "Повторы 300 м", Block("R", distance_m=300, recovery_m=300), (4, 8), {2, 3, 4}, f"{D44}, А4"),
    _t("R-400", "Повторы 400 м", Block("R", distance_m=400, recovery_m=400), (4, 10), {2, 3, 4},
       f"{D44}, А5, В4, С5"),
    _t("R-600", "Повторы 600 м", Block("R", distance_m=600, recovery_m=600), (2, 5), {2, 3, 4},
       f"{D44}, В5", min_week_km=50),
    Template(
        "R-200-400-200", "Повторы 200 и 400 м", WorkoutType.REPETITION,
        (Block("R", 4, distance_m=200, recovery_m=200), Block("R", 2, distance_m=400, recovery_m=400),
         Block("R", 4, distance_m=200, recovery_m=200)),
        frozenset({2, 3, 4}), f"{D44}, В3", None, min_week_km=50,
    ),
    _t("R-hills-30s", "Повторы в гору 30 с", Block("R", duration_s=30, recovery_s=90), (6, 12), {2, 3},
       f"{F7}, повторения на холмах", hill=True, note="в гору с усилием зоны R, вниз трусцой"),
    _t("R-hills-60s", "Повторы в гору 1 мин", Block("R", duration_s=MIN, recovery_s=2 * MIN), (6, 12), {2, 3},
       f"{F7}, повторения на холмах", hill=True, note="в гору с усилием зоны R, вниз трусцой"),

    # ---- Марафонский темп (М): фазы III и IV при подготовке к полумарафону и марафону ----
    _t("M-steady", "Бег в марафонском темпе", Block("M", duration_s=10 * MIN, continuous=True), (3, 8), {3, 4},
       "Дэниелс, табл. 4.1", note="непрерывно, внутри лёгкой пробежки"),
]}

# Основной тип качественной работы по фазам Дэниелса (гл. 10): подсказка модели
PHASE_FOCUS = {
    2: "основной тип: повторы (R); лёгкий быстрый финиш допустим",
    3: "основной тип: интервалы (I); дополнительно порог (T) и немного повторов (R)",
    4: "основной тип: порог (T); дополнительно интервалы (I) или повторы (R)",
}


# ---------------- Объём ----------------

def _mid_pace(zone: str, zones: Optional[TrainingZones]) -> Optional[float]:
    pace = pace_for_zone(zone, zones)
    return (pace.fast + pace.slow) / 2 if pace else None


def _rep_km(block: Block, zones: Optional[TrainingZones]) -> Optional[float]:
    """Длина одного отрезка в км. Отрезок по времени без VDOT не пересчитать: None."""
    if block.distance_m:
        return block.distance_m / 1000
    pace = _mid_pace(block.zone, zones)
    return block.duration_s / pace if pace else None


def _jog_sec_per_km(zones: Optional[TrainingZones]) -> float:
    """Восстановление трусцой: медленная граница лёгкого темпа (без VDOT условные 7:00 /км)."""
    return zones.easy.slow if zones is not None else DEFAULT_JOG_SEC_PER_KM


def _blocks_with_reps(template: Template, reps: Optional[int]) -> List[Block]:
    blocks = list(template.blocks)
    if template.reps_range and reps:
        first = blocks[0]
        blocks[0] = Block(first.zone, reps, first.distance_m, first.duration_s, first.recovery_s,
                          first.recovery_m, first.continuous, first.lead_jog_s)
    return blocks


@dataclass(frozen=True)
class Volume:
    work_km_by_zone: Dict[str, float]
    recovery_km: float

    @property
    def work_km(self) -> float:
        return round(sum(self.work_km_by_zone.values()), 2)


def volume(template: Template, reps: Optional[int], zones: Optional[TrainingZones]) -> Optional[Volume]:
    """Рабочая часть по зонам и восстановление в км. None, если без VDOT объём не посчитать."""
    by_zone: Dict[str, float] = {}
    recovery_km = 0.0
    jog = _jog_sec_per_km(zones)
    for block in _blocks_with_reps(template, reps):
        rep = _rep_km(block, zones)
        if rep is None:
            return None
        by_zone[block.zone] = by_zone.get(block.zone, 0.0) + rep * block.reps
        if not block.continuous:
            recovery_km += block.reps * ((block.recovery_m or 0) / 1000 + (block.recovery_s or 0) / jog)
        recovery_km += block.lead_jog_s / jog
    return Volume({z: round(km, 2) for z, km in by_zone.items()}, round(recovery_km, 2))


def min_distance_km(template: Template, vol: Volume) -> float:
    """Минимальная дистанция дня: рабочая часть, восстановление, разминка и заминка."""
    if template.finish:
        need = vol.work_km + FINISH_EASY_MIN_KM
    else:
        need = vol.work_km + vol.recovery_km + WARMUP_MIN_KM + COOLDOWN_MIN_KM
    return math.ceil(need * 10 - 1e-9) / 10   # вверх до 0,1 км


def _caps(week_km: float) -> Dict[str, float]:
    return {zone: min(share * week_km, absolute) for zone, wt in TYPE_BY_ZONE.items()
            for share, absolute in [LIMITS[wt]]}


def _fits(template: Template, vol: Volume, week_km: float) -> bool:
    """Каждая зона в своём лимите, и вся рабочая часть в лимите типа тренировки: так её проверит
    validate_week (quality_km смешанной тренировки T + R сверяется с лимитом T)."""
    caps = _caps(week_km)
    by_zone = all(km <= caps[zone] + KM_EPS for zone, km in vol.work_km_by_zone.items())
    type_zone = next(z for z, t in TYPE_BY_ZONE.items() if t == template.type)
    return by_zone and vol.work_km <= caps[type_zone] + KM_EPS


def reps_options(template: Template, week_km: float, zones: Optional[TrainingZones]) -> Optional[Tuple[int, int]]:
    """Допустимое число повторов при недельном объёме week_km: пределы шаблона, урезанные лимитами
    рабочей части (Дэниелс: T 10%, I 8%, R 5%, M 20% недели). None: тренировка в этот объём не помещается.
    Для фиксированной тренировки (1, 1), если помещается."""
    if template.reps_range is None:
        vol = volume(template, None, zones)
        return (1, 1) if vol is not None and _fits(template, vol, week_km) else None
    low, high = template.reps_range
    best = None
    for reps in range(low, high + 1):
        vol = volume(template, reps, zones)
        if vol is None or not _fits(template, vol, week_km):
            break
        best = reps
    return (low, best) if best is not None else None


def available(
    phase_number: int, week_km: float, zones: Optional[TrainingZones], race_m: Optional[float],
) -> List[Tuple[Template, Tuple[int, int]]]:
    """Тренировки каталога для недели: фаза, уровень по километражу, дистанция забега и лимиты объёма."""
    result = []
    for template in CATALOG.values():
        if phase_number not in template.phases or week_km + KM_EPS < template.min_week_km:
            continue
        if template.type == WorkoutType.MARATHON and (race_m or 0) < MARATHON_MIN_RACE_M:
            continue
        reps = reps_options(template, week_km, zones)
        if reps is not None:
            result.append((template, reps))
    return result


# ---------------- Описание ----------------

def _amount(block: Block, reps: int) -> str:
    if block.distance_m:
        meters = block.distance_m * reps if block.continuous else block.distance_m
        return f"{meters / 1000:g} км" if meters > 2000 else f"{meters} м"   # как у Дэниелса: 1000, 1600
    seconds = block.duration_s * reps if block.continuous else block.duration_s
    return f"{seconds // 60} мин" if seconds >= 60 and seconds % 60 == 0 else f"{seconds} с"


def _recovery(block: Block) -> str:
    if block.recovery_m:
        return f"{block.recovery_m} м трусцой"
    if block.recovery_s:
        seconds = block.recovery_s
        text = f"{seconds // 60} мин" if seconds % 60 == 0 else f"{seconds // 60} мин {seconds % 60} с"
        return f"{text} трусцой" if seconds >= 60 else f"{seconds} с трусцой"
    return ""


def _block_text(block: Block, hill: bool, count: Optional[str] = None) -> str:
    """count: подпись числа повторов вместо block.reps («N» в каталоге для модели)."""
    where = " в гору" if hill else ""
    if block.continuous and count:
        text = f"{count} × {_amount(block, 1)} слитно{where} в темпе {block.zone}"
    elif block.continuous or (block.reps == 1 and not count):
        text = f"{_amount(block, block.reps)}{where} в темпе {block.zone}"
    else:
        text = f"{count or block.reps} × {_amount(block, 1)}{where} в темпе {block.zone}"
        if _recovery(block):
            text += f", отдых {_recovery(block)}"
    if block.lead_jog_s:
        text = f"{block.lead_jog_s // 60} мин трусцой, затем {text}"
    return text


def describe(template: Template, reps: Optional[int]) -> str:
    """Описание дня для сообщения недели и календаря Garmin (без темпов: их подставляет код отдельно).
    reps None у тренировки с переменными повторами: «N × …» (так она показана модели в каталоге)."""
    count = "N" if template.reps_range and reps is None else None
    parts = [
        _block_text(b, template.hill, count if i == 0 else None)
        for i, b in enumerate(_blocks_with_reps(template, reps))
    ]
    body = "; ".join(parts)
    if template.finish:
        body = f"лёгкий бег, последние {body}"
    return f"{template.name}: {body}"


def catalog_prompt(options: List[Tuple[Template, Tuple[int, int]]], phase_number: int) -> str:
    """Список тренировок для промпта недели: id, структура и допустимые повторы (все числа от кода)."""
    lines = []
    for template, (low, high) in options:
        if template.reps_range is None:
            reps = "без повторов (reps не указывай)"
        else:
            reps = f"reps {low}" if low == high else f"reps от {low} до {high}"
        note = f"; {template.note}" if template.note else ""
        lines.append(f"  • {template.id} ({template.type.value}): {describe(template, None)}; {reps}{note}")
    focus = PHASE_FOCUS.get(phase_number)
    head = f"Фаза {phase_number}: {focus}." if focus else ""
    return "\n".join([head, *lines]) if head else "\n".join(lines)


# ---------------- Разбор недели ----------------

def resolve_day(
    day: PlannedDay, options: Dict[str, Tuple[int, int]], zones: Optional[TrainingZones],
) -> List[str]:
    """Качественный день по каталогу: проверка id, повторов и дистанции; заполняет quality_km и описание.

    options: доступные на этой неделе id -> допустимые повторы (из available)."""
    name = DAY_NAMES[day.day - 1]
    if not options:
        return [f"{name}: качественных тренировок на этой неделе нет (фаза или объём недели)"]
    if not day.workout_id:
        return [f"{name}: для качественной тренировки укажи workout_id из каталога"]
    template = CATALOG.get(day.workout_id)
    if template is None or day.workout_id not in options:
        return [f"{name}: workout_id '{day.workout_id}' нет среди доступных на этой неделе"]
    if template.type != day.type:
        return [f"{name}: {template.id} это тренировка типа {template.type.value}, а указан type {day.type.value}"]

    low, high = options[template.id]
    reps = day.reps
    if template.reps_range is None:
        reps = None
    elif reps is None:
        return [f"{name}: для {template.id} укажи reps от {low} до {high}"]
    elif not low <= reps <= high:
        return [f"{name}: reps {reps} для {template.id} вне допустимого: от {low} до {high}"]

    vol = volume(template, reps, zones)
    if vol is None:   # отсеивается в available, но на всякий случай
        return [f"{name}: {template.id} недоступна без VDOT"]
    need = min_distance_km(template, vol)
    if (day.distance_km or 0) + KM_EPS < need:
        return [f"{name}: для {template.id} (рабочая часть {vol.work_km:g} км, с восстановлением, разминкой "
                f"и заминкой) distance_km должна быть не меньше {need:g} км"]
    day.reps = reps
    day.quality_km = round(vol.work_km, 1)
    day.description = describe(template, reps)
    return []


def resolve_week(
    week: WeekPlan, options: Dict[str, Tuple[int, int]], zones: Optional[TrainingZones],
) -> List[str]:
    """Все качественные дни недели по каталогу. Пустой список: дни заполнены и готовы к validate_week."""
    problems: List[str] = []
    for day in week.days:
        if day.type in TYPE_BY_ZONE.values():
            problems += resolve_day(day, options, zones)
        elif day.workout_id:
            problems.append(f"{DAY_NAMES[day.day - 1]}: workout_id указывается только для качественных тренировок")
    return problems



# ---------------- Шаги для часов ----------------

@dataclass(frozen=True)
class Step:
    """Шаг тренировки для garmin_workouts: kind warmup | work | recovery | cooldown."""
    kind: str
    distance_m: Optional[float] = None
    duration_s: Optional[float] = None
    zone: Optional[str] = None
    target: bool = True


@dataclass(frozen=True)
class Repeat:
    iterations: int
    steps: Tuple[Step, ...] = field(default_factory=tuple)


def workout_steps(day: PlannedDay, zones: Optional[TrainingZones]) -> Optional[List[object]]:
    """Шаги дня из каталога: разминка, блоки (повторы с восстановлением), заминка. None: дня нет в каталоге.
    Лёгкая часть дня (дистанция минус работа и восстановление) делится поровну на разминку и заминку,
    у быстрого финиша вся уходит в разминку."""
    template = CATALOG.get(day.workout_id or "")
    if template is None or not day.distance_km:
        return None
    vol = volume(template, day.reps, zones)
    if vol is None:
        return None
    easy_km = max(0.0, day.distance_km - vol.work_km - (0 if template.finish else vol.recovery_km))

    steps: List[object] = []
    warmup_km = easy_km if template.finish else easy_km / 2
    if warmup_km >= 0.2:
        steps.append(Step("warmup", distance_m=round(warmup_km * 1000)))
    for block in _blocks_with_reps(template, day.reps):
        if block.lead_jog_s:
            steps.append(Step("recovery", duration_s=block.lead_jog_s))
        if block.continuous or block.reps == 1:
            work = Step("work", distance_m=block.distance_m * block.reps if block.distance_m else None,
                        duration_s=block.duration_s * block.reps if block.duration_s else None,
                        zone=block.zone, target=not template.hill)
            steps.append(work)
            continue
        work = Step("work", distance_m=block.distance_m, duration_s=block.duration_s, zone=block.zone,
                    target=not template.hill)
        rest = Step("recovery", distance_m=block.recovery_m, duration_s=block.recovery_s)
        steps.append(Repeat(block.reps, (work, rest) if (block.recovery_m or block.recovery_s) else (work,)))
    if not template.finish and easy_km / 2 >= 0.2:
        steps.append(Step("cooldown", distance_m=round(easy_km / 2 * 1000)))
    return steps


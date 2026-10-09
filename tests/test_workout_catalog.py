import pytest

from schemas.plan import PlannedDay, WeekPlan, WorkoutType
from services.coach_service import calculate_zones
from services.workout_catalog import (
    CATALOG,
    COOLDOWN_MIN_KM,
    LIMITS,
    TYPE_BY_ZONE,
    WARMUP_MIN_KM,
    Repeat,
    Step,
    available,
    catalog_prompt,
    describe,
    min_distance_km,
    reps_options,
    resolve_day,
    resolve_week,
    volume,
    workout_steps,
)

ZONES = calculate_zones(45)
HALF_M = 21097.5


# ---------- целостность каталога ----------

@pytest.mark.parametrize("template", list(CATALOG.values()), ids=list(CATALOG))
def test_catalog_entries_are_consistent(template):
    assert template.phases and template.phases <= {2, 3, 4}          # в фазе I качественных нет
    assert template.source
    if template.reps_range:
        low, high = template.reps_range
        assert 1 <= low <= high
    # тип тренировки = зона первого блока, лимит для него задан
    assert TYPE_BY_ZONE[template.blocks[0].zone] == template.type
    assert template.type in LIMITS
    assert describe(template, None).startswith(template.name)
    assert volume(template, None, ZONES) is not None


def test_phase_one_has_no_quality_and_phase_focus_follows_daniels():
    assert available(1, 50, ZONES, HALF_M) == []
    phase2 = {t.type for t, _ in available(2, 60, ZONES, HALF_M)}
    assert WorkoutType.INTERVAL not in phase2 and WorkoutType.REPETITION in phase2   # фаза II: повторы
    phase3 = {t.type for t, _ in available(3, 60, ZONES, HALF_M)}
    assert WorkoutType.INTERVAL in phase3


def test_marathon_pace_only_for_half_marathon_and_longer():
    def ids(race_m):
        return {t.id for t, _ in available(3, 60, ZONES, race_m)}
    assert "M-steady" in ids(HALF_M)
    assert "M-steady" not in ids(10000)
    assert "M-steady" not in ids(None)


def test_level_by_weekly_km():
    low = {t.id for t, _ in available(3, 40, ZONES, HALF_M)}
    high = {t.id for t, _ in available(3, 80, ZONES, HALF_M)}
    assert "I-1200" not in low and "I-1200" in high       # Дэниелс: 1200 м с 64 км в неделю


def test_time_based_workouts_need_vdot():
    ids = {t.id for t, _ in available(3, 60, None, HALF_M)}
    assert "I-1000" in ids and "R-400" in ids
    assert "I-3min" not in ids and "T-tempo-20" not in ids


# ---------- объём и повторы ----------

def test_reps_limited_by_weekly_caps():
    # R: 5% недели. 36 км -> 1.8 км -> 4 × 400; 60 км -> 3 км -> 7 × 400
    assert reps_options(CATALOG["R-400"], 36, None) == (4, 4)
    assert reps_options(CATALOG["R-400"], 60, None) == (4, 7)
    # I: 8% недели. 40 км -> 3.2 км -> 4 × 800 (Дэниелс, табл. 4.3 А4: 4–5 × 800)
    assert reps_options(CATALOG["I-800"], 40, ZONES) == (4, 4)
    # не помещается даже минимум: недоступна
    assert reps_options(CATALOG["R-400"], 30, None) is None


def test_fixed_workout_fits_or_not():
    tempo = CATALOG["T-tempo-20"]
    work = volume(tempo, None, ZONES).work_km_by_zone["T"]
    assert reps_options(tempo, work / 0.10 + 1, ZONES) == (1, 1)
    assert reps_options(tempo, work / 0.10 - 5, ZONES) is None    # 10% недели меньше 20 мин порога


def test_volume_time_reps_from_zone_pace_and_recovery_from_easy_pace():
    vol = volume(CATALOG["I-3min"], 5, ZONES)
    pace = (ZONES.interval.fast + ZONES.interval.slow) / 2
    assert vol.work_km_by_zone == {"I": round(5 * 180 / pace, 2)}
    assert vol.recovery_km == round(5 * 120 / ZONES.easy.slow, 2)


def test_mixed_workout_volume_by_zone():
    vol = volume(CATALOG["T-tempo-20-R200"], None, ZONES)
    assert set(vol.work_km_by_zone) == {"T", "R"}
    assert vol.work_km_by_zone["R"] == 0.8
    # трусца 3 мин перед повторами и 4 × 200 м
    assert vol.recovery_km == round(180 / ZONES.easy.slow + 0.8, 2)


def test_min_distance():
    tmpl = CATALOG["R-400"]
    vol = volume(tmpl, 5, None)
    assert min_distance_km(tmpl, vol) == round(2.0 + 2.0 + WARMUP_MIN_KM + COOLDOWN_MIN_KM, 1)
    finish = CATALOG["T-finish"]
    assert min_distance_km(finish, volume(finish, 2, ZONES)) == pytest.approx(
        volume(finish, 2, ZONES).work_km + 3.0, abs=0.1)


# ---------- описание ----------

def test_describe():
    assert describe(CATALOG["I-1000"], 5) == "Интервалы 1000 м: 5 × 1000 м в темпе I, отдых 3 мин трусцой"
    assert describe(CATALOG["T-tempo-20"], None) == "Темповый бег 20 мин: 20 мин в темпе T"
    assert describe(CATALOG["T-finish"], 2) == "Быстрый финиш: лёгкий бег, последние 10 мин в темпе T"
    assert describe(CATALOG["R-hills-30s"], 8) == (
        "Повторы в гору 30 с: 8 × 30 с в гору в темпе R, отдых 1 мин 30 с трусцой")
    assert describe(CATALOG["T-cruise-3200"], 2) == (
        "Крейсерские интервалы 3,2 км: 2 × 3.2 км в темпе T, отдых 2 мин трусцой")
    assert describe(CATALOG["T-tempo-20-R200"], None) == (
        "Темповый бег 20 мин + повторы 200 м: 20 мин в темпе T; "
        "3 мин трусцой, затем 4 × 200 м в темпе R, отдых 200 м трусцой")


def test_catalog_prompt_lists_ids_and_reps():
    options = available(3, 40, ZONES, HALF_M)
    text = catalog_prompt(options, 3)
    assert text.startswith("Фаза 3: основной тип: интервалы (I)")
    # число повторов выбирает модель: в структуре «N ×», пределы отдельно
    assert "• I-800 (interval): Интервалы 800 м: N × 800 м в темпе I, отдых 2 мин трусцой; reps 4\n" in text
    assert "• I-2min (interval): Интервалы 2 мин: N × 2 мин в темпе I, отдых 1 мин трусцой; reps от 5 до 6" in text
    assert "Быстрый финиш: лёгкий бег, последние N × 5 мин слитно в темпе T; reps от 1 до 3" in text


# ---------- разбор дня ----------

def day(**kw):
    data = {"day": 2, "type": "interval", "distance_km": 10, "workout_id": "I-800", "reps": 4}
    data.update(kw)
    return PlannedDay.model_validate(data)


OPTIONS = {t.id: reps for t, reps in available(3, 40, ZONES, HALF_M)}


def test_resolve_day_fills_quality_and_description():
    d = day()
    assert resolve_day(d, OPTIONS, ZONES) == []
    assert d.quality_km == 3.2
    assert d.description == "Интервалы 800 м: 4 × 800 м в темпе I, отдых 2 мин трусцой"


@pytest.mark.parametrize("kw,message", [
    ({"workout_id": None, "quality_km": 3}, "укажи workout_id"),
    ({"workout_id": "I-9999"}, "нет среди доступных"),
    ({"workout_id": "I-1200"}, "нет среди доступных"),            # не по уровню 40 км
    ({"type": "threshold"}, "тренировка типа interval"),
    ({"reps": 6}, "reps 6 для I-800 вне допустимого: от 4 до 4"),
    ({"reps": None}, "укажи reps от 4 до 4"),
    ({"distance_km": 5}, "distance_km должна быть не меньше"),
])
def test_resolve_day_problems(kw, message):
    problems = resolve_day(day(**kw), OPTIONS, ZONES)
    assert len(problems) == 1 and message in problems[0]
    assert problems[0].startswith("Вт: ")


def test_resolve_day_fixed_workout_ignores_reps():
    options = {t.id: r for t, r in available(4, 60, ZONES, HALF_M)}
    d = day(type="threshold", workout_id="T-tempo-20", reps=3)
    assert resolve_day(d, options, ZONES) == []
    assert d.reps is None and d.description == "Темповый бег 20 мин: 20 мин в темпе T"


def test_resolve_week_rejects_workout_id_on_easy_day_and_empty_catalog():
    week = WeekPlan.model_validate({"days": [
        {"day": 1, "type": "rest"},
        {"day": 2, "type": "easy", "distance_km": 8, "workout_id": "I-800"},
        {"day": 3, "type": "interval", "distance_km": 10, "workout_id": "I-800", "reps": 4},
        {"day": 4, "type": "rest"}, {"day": 5, "type": "easy", "distance_km": 6},
        {"day": 6, "type": "long", "distance_km": 12}, {"day": 7, "type": "rest"},
    ]})
    problems = resolve_week(week, {}, ZONES)
    assert problems == [
        "Вт: workout_id указывается только для качественных тренировок",
        "Ср: качественных тренировок на этой неделе нет (фаза или объём недели)",
    ]


# ---------- шаги для часов ----------

def test_steps_for_distance_intervals():
    d = day(distance_km=10)
    resolve_day(d, OPTIONS, ZONES)
    steps = workout_steps(d, ZONES)
    warmup, repeat, cooldown = steps
    assert isinstance(repeat, Repeat) and repeat.iterations == 4
    assert repeat.steps == (Step("work", distance_m=800, zone="I"), Step("recovery", duration_s=120))
    assert warmup.kind == "warmup" and cooldown.kind == "cooldown"
    vol = volume(CATALOG["I-800"], 4, ZONES)
    assert warmup.distance_m == cooldown.distance_m == round((10 - vol.work_km - vol.recovery_km) / 2 * 1000)


def test_steps_for_finish_hills_and_mixed():
    finish = workout_steps(day(type="threshold", workout_id="T-finish", reps=2, distance_km=8), ZONES)
    assert [s.kind for s in finish] == ["warmup", "work"]                 # без заминки
    assert finish[1] == Step("work", duration_s=600, zone="T")

    hills = workout_steps(day(type="repetition", workout_id="R-hills-60s", reps=8, distance_km=9), ZONES)
    assert hills[1].steps[0].target is False                              # в гору без цели по темпу

    mixed = workout_steps(day(type="threshold", workout_id="T-tempo-20-R200", reps=None, distance_km=12), ZONES)
    assert [type(s).__name__ for s in mixed] == ["Step", "Step", "Step", "Repeat", "Step"]
    assert mixed[1] == Step("work", duration_s=1200, zone="T")
    assert mixed[2] == Step("recovery", duration_s=180)
    assert mixed[3].steps == (Step("work", distance_m=200, zone="R"), Step("recovery", distance_m=200))


def test_steps_none_for_day_without_catalog():
    assert workout_steps(PlannedDay(day=2, type="interval", distance_km=10, quality_km=4), ZONES) is None


def test_mixed_workout_total_fits_type_limit_like_validator():
    # 20 мин T + 4 × 200 R: validate_week сверяет всю рабочую часть с лимитом T (10% недели)
    tmpl = CATALOG["T-tempo-20-R200"]
    total = volume(tmpl, None, ZONES).work_km
    t_only = volume(tmpl, None, ZONES).work_km_by_zone["T"]
    week_km = (t_only + total) / 2 / 0.10          # T помещается, T + R нет
    assert reps_options(tmpl, week_km, ZONES) is None
    assert reps_options(tmpl, total / 0.10 + 1, ZONES) == (1, 1)

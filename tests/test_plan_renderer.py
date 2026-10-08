from datetime import date

from schemas.plan import MacroPlan, WeekPlan
from services.coach_service import calculate_zones
from services.plan_renderer import render_macro, render_week

ZONES = calculate_zones(50)


def make_week(description="Разминка, 3 км в пороговом темпе, заминка"):
    return WeekPlan.model_validate({
        "days": [
            {"day": 1, "type": "rest", "description": "Отдых"},
            {"day": 2, "type": "threshold", "distance_km": 8, "quality_km": 3,
             "duration_min": 55, "description": description},
            {"day": 3, "type": "easy", "distance_km": 5},
            {"day": 4, "type": "easy", "distance_km": 6},
            {"day": 5, "type": "cross"},
            {"day": 6, "type": "long", "distance_km": 10, "duration_min": 70},
            {"day": 7, "type": "easy", "distance_km": 7},
        ],
        "note": "Следи за самочувствием",
    })


def make_macro(notes=None):
    return MacroPlan.model_validate({
        "phases": [
            {"number": 1, "name": "Фундамент", "weeks": 3, "focus": "Аэробная база"},
            {"number": 2, "name": "Раннее качество", "weeks": 4},
            {"number": 3, "name": "Пиковое качество", "weeks": 4},
            {"number": 4, "name": "Подводка", "weeks": 1},
        ],
        "weekly_km": [30, 32, 34, 36, 38, 40, 32, 42, 44, 46, 36, 25],
        "notes": notes or [],
    })


def render(week, **overrides):
    params = dict(
        target_race="21.1 км (Полумарафон)", week_number=5, total_weeks=12,
        week_start="01.03.2027", week_end="07.03.2027", zones=ZONES, max_hr=200,
    )
    params.update(overrides)
    return render_week(week, **params)


def assert_balanced(text):
    for tag in ("b", "i", "code"):
        assert text.count(f"<{tag}>") == text.count(f"</{tag}>"), tag


# ---------- render_week ----------

def test_week_header_and_summary():
    text = render(make_week())
    assert "Неделя №5 из 12" in text
    assert "01.03.2027 – 07.03.2027" in text
    assert "Объём: <b>36 км</b>" in text
    assert "рабочая часть качественных: 3 км" in text


def test_threshold_day_has_pace_and_hr_from_code():
    text = render(make_week())
    assert f"<code>{ZONES.threshold}</code> (рабочая часть)" in text
    assert "<code>176–184 уд/мин</code>" in text


def test_easy_day_uses_easy_pace_range():
    assert f"<code>{ZONES.easy}</code>" in render(make_week())


def test_rest_and_cross_days_have_no_pace():
    text = render(make_week())
    rest_block = text.split("\n\n")[1]          # первый день после шапки
    assert "Отдых" in rest_block
    assert "/км" not in rest_block


def test_rest_description_is_not_duplicated():
    text = render(make_week())
    assert text.count("Отдых") == 1


def test_model_text_is_escaped():
    text = render(make_week(description="5 < 6 & <b>жирно</b>"))
    assert "5 &lt; 6 &amp; &lt;b&gt;жирно&lt;/b&gt;" in text
    assert_balanced(text)


def test_without_zones_shows_hint_and_no_paces():
    text = render(make_week(), zones=None)
    assert "/sync" in text
    assert "/км" not in text


def test_without_max_hr_no_pulse():
    assert "уд/мин" not in render(make_week(), max_hr=None)


def test_note_is_rendered():
    assert "Следи за самочувствием" in render(make_week())


def test_week_html_is_balanced():
    assert_balanced(render(make_week()))


# ---------- render_macro ----------

def test_macro_phase_ranges_and_km():
    text = render_macro(make_macro(), "21.1 км (Полумарафон)", date(2027, 6, 15), ZONES)
    assert "нед. 1–3" in text
    assert "нед. 4–7" in text
    assert "нед. 8–11" in text
    assert "(нед. 12)" in text
    assert "Километраж: 30 · 32 · 34 км" in text
    assert "Забег: 15.06.2027" in text
    assert "пик 46 км/нед" in text


def test_macro_includes_zones_block():
    text = render_macro(make_macro(), "21.1 км", None, ZONES)
    assert "Ваши зоны темпа" in text
    assert str(ZONES.threshold) in text


def test_macro_skips_zero_week_phases():
    macro = MacroPlan.model_validate({
        "phases": [
            {"number": 1, "name": "Фундамент", "weeks": 0},
            {"number": 2, "name": "Раннее качество", "weeks": 0},
            {"number": 3, "name": "Пиковое качество", "weeks": 1},
            {"number": 4, "name": "Подводка", "weeks": 1},
        ],
        "weekly_km": [30, 20],
    })
    text = render_macro(macro, "10 км")
    assert "Фундамент" not in text
    assert "Фаза 3" in text and "(нед. 1)" in text
    assert "Фаза 4" in text and "(нед. 2)" in text


def test_macro_notes_and_escaping():
    text = render_macro(make_macro(notes=["Боль > 3 из 10: отдых"]), "5 <км>")
    assert "• Боль &gt; 3 из 10: отдых" in text
    assert "5 &lt;км&gt;" in text
    assert_balanced(text)
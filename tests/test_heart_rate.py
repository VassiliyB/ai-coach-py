from types import SimpleNamespace

import pytest

from services.heart_rate import (
    HR_FRACTIONS,
    KIND_LTHR,
    KIND_MAX,
    LTHR_FRACTIONS,
    HeartRateBasis,
    PulseInput,
    as_basis,
    check_pulse,
    parse_pulse,
    profile_hr_basis,
    render_pulse,
)
from services.plan_paces import hr_range_for_zone

LTHR = HeartRateBasis(170, KIND_LTHR, manual=True)


def test_max_basis_matches_previous_int_behaviour():
    assert hr_range_for_zone("E", 200) == (130, 158)
    assert hr_range_for_zone("E", HeartRateBasis(200)) == (130, 158)
    assert hr_range_for_zone("R", HeartRateBasis(200)) is None
    assert hr_range_for_zone(None, HeartRateBasis(200)) is None
    assert hr_range_for_zone("E", None) is None


def test_lthr_zones_follow_fitzgerald():
    # E = зоны 1–2 (75–89% ПАНО), M = зона X (90–95%), T = зона 3 (96–100%), I = зона 4 (102–105%)
    assert LTHR.range_for_zone("E") == (128, 151)
    assert LTHR.range_for_zone("M") == (153, 162)
    assert LTHR.range_for_zone("T") == (163, 170)
    assert LTHR.range_for_zone("I") == (173, 178)
    assert LTHR.range_for_zone("R") is None


def test_lthr_zones_close_to_max_zones_at_typical_ratio():
    # При ПАНО ≈ 89% ЧССmax верх лёгкой зоны в обеих системах почти совпадает
    max_hr = 190
    lthr = HeartRateBasis(round(max_hr * 0.89), KIND_LTHR)
    assert abs(lthr.range_for_zone("E")[1] - HeartRateBasis(max_hr).range_for_zone("E")[1]) <= 2
    assert set(LTHR_FRACTIONS) == set(HR_FRACTIONS)


@pytest.mark.parametrize("hr, expected", [
    (120, ("below_E", 71)), (140, ("E", 82)), (155, ("M", 91)), (166, ("T", 98)), (175, ("I", 103)),
    (172, ("T", 101)),   # между T и I: зона ниже
])
def test_zone_of_lthr(hr, expected):
    assert LTHR.zone_of(hr) == expected


def test_as_basis():
    assert as_basis(None) is None
    assert as_basis(0) is None
    assert as_basis(185) == HeartRateBasis(185, KIND_MAX, manual=False)
    assert as_basis(LTHR) is LTHR


def test_profile_basis_priority():
    def profile(peak=None, manual=None, lthr=None):
        return SimpleNamespace(max_heart_rate=peak, manual_max_hr=manual, lthr=lthr)

    assert profile_hr_basis(None) is None
    assert profile_hr_basis(profile()) is None
    assert profile_hr_basis(profile(peak=182)) == HeartRateBasis(182)
    assert profile_hr_basis(profile(peak=182, manual=190)) == HeartRateBasis(190, KIND_MAX, manual=True)
    assert profile_hr_basis(profile(peak=182, manual=190, lthr=168)) == HeartRateBasis(168, KIND_LTHR, manual=True)


def test_labels():
    assert HeartRateBasis(188).label == "ЧССmax 188"
    assert LTHR.label == "пульса ПАНО 170"
    assert "Garmin" in HeartRateBasis(188).source_text
    assert "вручную" in HeartRateBasis(188, manual=True).source_text


@pytest.mark.parametrize("text, expected", [
    ("188", PulseInput(KIND_MAX, 188)),
    ("max 188", PulseInput(KIND_MAX, 188)),
    ("Макс 188", PulseInput(KIND_MAX, 188)),
    ("чссmax: 188", PulseInput(KIND_MAX, 188)),
    ("пано 172", PulseInput(KIND_LTHR, 172)),
    ("ПАНО172", PulseInput(KIND_LTHR, 172)),
    ("порог 172", PulseInput(KIND_LTHR, 172)),
    ("lthr 172", PulseInput(KIND_LTHR, 172)),
    ("сброс", PulseInput(None)),
])
def test_parse_pulse(text, expected):
    assert parse_pulse(text) == expected


@pytest.mark.parametrize("text", ["", "пульс", "покой 50", "188 190", "1888", "пано"])
def test_parse_pulse_rejects(text):
    assert parse_pulse(text) is None


def test_check_pulse_ranges():
    assert check_pulse(PulseInput(KIND_MAX, 188), None, None) is None
    assert "от 140" in check_pulse(PulseInput(KIND_MAX, 120), None, None)
    assert "от 140" in check_pulse(PulseInput(KIND_MAX, 240), None, None)
    assert check_pulse(PulseInput(KIND_LTHR, 170), None, None) is None
    assert "ПАНО должен" in check_pulse(PulseInput(KIND_LTHR, 100), None, None)
    assert check_pulse(PulseInput(None), 190, 170) is None


def test_check_pulse_consistency_between_max_and_lthr():
    assert check_pulse(PulseInput(KIND_LTHR, 168), 190, None) is None
    assert "не согласуется" in check_pulse(PulseInput(KIND_LTHR, 188), 190, None)    # ПАНО почти равен максимуму
    assert "не согласуется" in check_pulse(PulseInput(KIND_LTHR, 130), 190, None)    # 68% максимума
    assert "не согласуется" in check_pulse(PulseInput(KIND_MAX, 172), None, 170)
    assert check_pulse(PulseInput(KIND_MAX, 190), None, 170) is None


def test_render_pulse():
    text = render_pulse(LTHR, manual_max=190, lthr=170)
    assert "пульс ПАНО <b>170</b>" in text
    assert "E · лёгкий: <code>128–151 уд/мин</code>" in text
    assert "ЧССmax 190 сохранён" in text
    assert "/pulse пано" in text
    assert "не заданы" in render_pulse(None)


def test_help_shows_current_values_next_to_examples():
    # Пример «max 188» не должен выглядеть как сохранённое значение: рядом текущее
    text = render_pulse(HeartRateBasis(190, manual=True), manual_max=190)
    assert "максимальный пульс (сейчас 190)" in text
    assert "самый точный вариант;" in text          # ПАНО не задан: без «сейчас»
    assert "(сейчас" not in render_pulse(HeartRateBasis(180))


def test_after_save_no_input_examples():
    text = render_pulse(HeartRateBasis(190, manual=True), manual_max=190, with_help=False)
    assert "/pulse max 188" not in text
    assert "Изменить: <code>/pulse</code>" in text
    assert "ЧССmax <b>190</b> (задан вручную)" in text

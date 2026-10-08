import pytest

from services.coach_service import (
    calculate_vdot, calculate_zones, predict_race_time,
)


def test_vdot_5k_19_57_is_about_50():
    assert calculate_vdot(5000, 19 * 60 + 57) == pytest.approx(50.0, abs=0.5)


def test_threshold_pace_vdot_50():
    z = calculate_zones(50)
    assert z.threshold.fast == pytest.approx(4 * 60 + 15, abs=3)


def test_predict_roundtrip():
    assert predict_race_time(50, 5000) == pytest.approx(19 * 60 + 57, abs=5)


def test_zones_are_ordered():
    z = calculate_zones(48)
    assert z.easy.slow > z.easy.fast > z.marathon.fast > z.threshold.fast \
        > z.interval.fast > z.repetition.fast


@pytest.mark.parametrize("dist,time", [(0, 100), (5000, 0), (-1, 10)])
def test_invalid_input(dist, time):
    with pytest.raises(ValueError):
        calculate_vdot(dist, time)


def test_vdot_out_of_range():
    with pytest.raises(ValueError):
        calculate_zones(120)
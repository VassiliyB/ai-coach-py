"""Базы знаний (полная для Claude, краткая для Groq) не расходятся с порогами, которые проверяет код."""
from pathlib import Path

import pytest

from schemas.plan import WorkoutType
from services.heart_rate import HR_FRACTIONS
from services.plan_validator import LONG_MAX_MINUTES, LONG_MAX_SHARE, MAX_WEEKLY_GROWTH, QUALITY_LIMITS
from services.workout_catalog import MARATHON_LIMIT

ROOT = Path(__file__).resolve().parent.parent
FULL = (ROOT / "sports_knowledge.txt").read_text(encoding="utf-8")
SHORT = (ROOT / "sports_knowledge_short.txt").read_text(encoding="utf-8")


def _pct(value: float) -> str:
    return f"{round(value * 100)}%"


def test_short_is_much_shorter():
    # Смысл краткой версии: системный промпт Groq с запасом помещается в 8000 токенов в минуту
    assert len(SHORT) < len(FULL) / 2.5
    assert len(SHORT) < 7000


@pytest.mark.parametrize("text", [FULL, SHORT], ids=["full", "short"])
def test_hr_fractions_match_code(text):
    for zone, (low, high) in HR_FRACTIONS.items():
        assert f"{round(low * 100)}–{_pct(high)} ЧССmax" in text, zone


def test_short_limits_match_validator():
    t_share, t_km = QUALITY_LIMITS[WorkoutType.THRESHOLD]
    i_share, i_km = QUALITY_LIMITS[WorkoutType.INTERVAL]
    r_share, r_km = QUALITY_LIMITS[WorkoutType.REPETITION]
    m_share, m_km = MARATHON_LIMIT
    assert f"Не больше {_pct(t_share)} недели и {t_km:g} км" in SHORT
    assert f"Не больше {_pct(i_share)} недели и {i_km:g} км" in SHORT
    assert f"Не больше {_pct(r_share)} недели и {r_km:g} км" in SHORT
    assert f"не больше {_pct(m_share)} недели и {m_km:g} км" in SHORT
    assert f"до {_pct(LONG_MAX_SHARE)} недели и не дольше {LONG_MAX_MINUTES} мин" in SHORT
    assert f"Рост не больше {_pct(MAX_WEEKLY_GROWTH)}" in SHORT


def test_short_keeps_every_section():
    for title in ("ЗОНЫ ДЭНИЕЛСА", "80/20", "ФАЗЫ", "НЕДЕЛЯ", "ОБЪЁМ", "РАЗБОР ТРЕНИРОВКИ",
                  "ПЕРЕРЫВЫ", "УСЛОВИЯ", "БЕЗОПАСНОСТЬ"):
        assert title in SHORT, title

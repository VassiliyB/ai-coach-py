import json

import pytest
from pydantic import ValidationError

from schemas.plan import MacroPlan, WeekPlan, WorkoutType
from services.plan_storage import LEGACY_KEY, parse_macro, parse_week, plan_to_details
from tests.test_plan_schemas import valid_macro, valid_week_days


def make_macro():
    return MacroPlan.model_validate(valid_macro())


def make_week():
    return WeekPlan.model_validate({"days": valid_week_days(), "note": "Неделя втягивания"})

def test_macro_round_trip():
    macro = make_macro()
    details = plan_to_details(macro)
    json.dumps(details)  # значение должно быть сериализуемо в JSONB
    assert parse_macro(details) == macro


def test_week_round_trip_keeps_types_as_strings():
    week = make_week()
    details = plan_to_details(week)
    assert details["days"][1]["type"] == "threshold"
    restored = parse_week(json.loads(json.dumps(details)))
    assert restored == week
    assert restored.days[1].type is WorkoutType.THRESHOLD
    assert restored.total_km == week.total_km


def test_legacy_and_empty_parse_to_none():
    # так миграция перенесла старые текстовые планы
    assert parse_macro({LEGACY_KEY: "старый план"}) is None
    assert parse_week({LEGACY_KEY: "старая неделя"}) is None
    assert parse_macro(None) is None


def test_broken_structured_plan_raises():
    details = plan_to_details(make_macro())
    details["weekly_km"] = details["weekly_km"][:-1]  # не сходится с суммой недель по фазам
    with pytest.raises(ValidationError):
        parse_macro(details)

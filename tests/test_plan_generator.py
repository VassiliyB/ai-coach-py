import asyncio
import json
import math
from datetime import date

import pytest

from clients.ai_errors import AIClientError, AIResponseFormatError
from schemas.plan import MacroPlan, WeekPlan
from services.coach_service import calculate_zones
from services.plan_generator import (
    MAX_ATTEMPTS,
    PlanGenerationError,
    PlanGenerator,
    intro_long_cap_km,
    intro_target_km,
    parse_json_object,
)
from services.plan_validator import long_run_max_km


class FakeAI:
    """Подставной клиент LLM: отдаёт заранее заданные ответы и запоминает вызовы."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def generate_response(
        self, messages, model=None, temperature=0.4, max_tokens=3000, json_mode=False, response_schema=None,
    ):
        self.calls.append({"messages": messages, "json_mode": json_mode, "response_schema": response_schema})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def macro_data():
    return {
        "phases": [
            {"number": 1, "name": "Фундамент", "weeks": 3},
            {"number": 2, "name": "Раннее качество", "weeks": 4},
            {"number": 3, "name": "Пиковое качество", "weeks": 4},
            {"number": 4, "name": "Подводка", "weeks": 1},
        ],
        "weekly_km": [30, 32, 34, 36, 38, 40, 32, 42, 44, 46, 36, 25],
    }


def good_week_data():
    return {
        "days": [
            {"day": 1, "type": "rest"},
            {"day": 2, "type": "threshold", "distance_km": 8, "quality_km": 3},
            {"day": 3, "type": "easy", "distance_km": 5},
            {"day": 4, "type": "easy", "distance_km": 6},
            {"day": 5, "type": "rest"},
            {"day": 6, "type": "long", "distance_km": 10},
            {"day": 7, "type": "easy", "distance_km": 7},
        ]
    }


def dumps(data):
    return json.dumps(data, ensure_ascii=False)


def generator(responses):
    ai = FakeAI(responses)
    return PlanGenerator(ai_client=ai, knowledge_base=""), ai


PROFILE = {"summary_text": "Средненедельный объем: 30 км/нед"}


# ---------- parse_json_object ----------

def test_parse_json_with_code_fence():
    raw = 'Вот план:\n```json\n{"a": 1}\n```'
    assert parse_json_object(raw) == {"a": 1}


def test_parse_json_without_object_raises():
    with pytest.raises(ValueError):
        parse_json_object("просто текст")


# ---------- generate_macro ----------

def test_macro_first_try():
    gen, ai = generator([dumps(macro_data())])
    macro = asyncio.run(gen.generate_macro(PROFILE, "21.1 км", "2027-06-15", 12))
    assert isinstance(macro, MacroPlan)
    assert len(ai.calls) == 1
    assert ai.calls[0]["json_mode"] is True
    assert ai.calls[0]["response_schema"] is MacroPlan   # для структурированного вывода Claude


def test_macro_retry_on_validator_problems():
    bad = macro_data()
    bad["weekly_km"][3] = 50                       # слишком быстрый рост
    gen, ai = generator([dumps(bad), dumps(macro_data())])
    macro = asyncio.run(gen.generate_macro(PROFILE, "21.1 км", "2027-06-15", 12))
    assert macro.total_weeks == 12
    assert len(ai.calls) == 2
    assert len(ai.calls[0]["messages"]) == 2       # исходный список не мутируется
    retry_messages = ai.calls[1]["messages"]
    assert retry_messages[-2]["role"] == "assistant"
    assert "неделя 4" in retry_messages[-1]["content"]


def test_macro_gives_up_after_max_attempts():
    bad = macro_data()
    bad["weekly_km"][3] = 50
    gen, ai = generator([dumps(bad)] * MAX_ATTEMPTS)
    with pytest.raises(PlanGenerationError) as exc_info:
        asyncio.run(gen.generate_macro(PROFILE, "21.1 км", "2027-06-15", 12))
    assert len(ai.calls) == MAX_ATTEMPTS
    assert exc_info.value.problems


def test_retry_on_invalid_json():
    gen, ai = generator(["это не JSON", dumps(macro_data())])
    macro = asyncio.run(gen.generate_macro(PROFILE, "21.1 км", "2027-06-15", 12))
    assert macro.total_weeks == 12
    assert "JSON" in ai.calls[1]["messages"][-1]["content"]


def test_retry_after_unusable_response_repeats_same_request():
    # Groq отклонил JSON (json_validate_failed) или ответ обрезан: повторяем тот же запрос без замечаний
    gen, ai = generator([AIResponseFormatError("ИИ вернул некорректный JSON"), dumps(macro_data())])
    macro = asyncio.run(gen.generate_macro(PROFILE, "21.1 км", "2027-06-15", 12))
    assert macro.total_weeks == 12
    assert len(ai.calls) == 2
    assert ai.calls[1]["messages"] == ai.calls[0]["messages"]


def test_unusable_responses_exhaust_attempts():
    gen, ai = generator([AIResponseFormatError("обрезан по длине")] * MAX_ATTEMPTS)
    with pytest.raises(PlanGenerationError) as exc_info:
        asyncio.run(gen.generate_macro(PROFILE, "21.1 км", "2027-06-15", 12))
    assert len(ai.calls) == MAX_ATTEMPTS
    assert exc_info.value.problems == ["обрезан по длине"]


def test_other_ai_errors_are_not_retried():
    gen, ai = generator([AIClientError("Сервер перегружен запросами"), dumps(macro_data())])
    with pytest.raises(AIClientError):
        asyncio.run(gen.generate_macro(PROFILE, "21.1 км", "2027-06-15", 12))
    assert len(ai.calls) == 1


def test_macro_total_weeks_mismatch_triggers_retry():
    # просим 10 недель, а модель каждый раз отвечает планом на 12
    gen, ai = generator([dumps(macro_data())] * MAX_ATTEMPTS)
    with pytest.raises(PlanGenerationError) as exc_info:
        asyncio.run(gen.generate_macro(PROFILE, "21.1 км", "2027-06-15", 10))
    assert any("недель" in p for p in exc_info.value.problems)


# ---------- generate_week ----------

def test_week_generation_uses_target_and_phase():
    macro = MacroPlan.model_validate(macro_data())
    gen, ai = generator([dumps(good_week_data())])
    week = asyncio.run(gen.generate_week(PROFILE, "21.1 км", macro, 5, "01.03.2027", "07.03.2027"))
    assert week.total_km == 36.0
    prompt = ai.calls[0]["messages"][-1]["content"]
    assert "38" in prompt                          # плановый километраж 5-й недели
    assert "Фаза 2" in prompt                      # 5-я неделя попадает во вторую фазу


def test_week_retry_on_schema_error():
    broken = good_week_data()
    broken["days"] = broken["days"][:6]            # только 6 дней
    macro = MacroPlan.model_validate(macro_data())
    gen, ai = generator([dumps(broken), dumps(good_week_data())])
    week = asyncio.run(gen.generate_week(PROFILE, "21.1 км", macro, 5, "01.03.2027", "07.03.2027"))
    assert len(week.days) == 7
    assert len(ai.calls) == 2


def base_week_data():
    # 30 км без качественных тренировок: подходит для 1-й недели (фаза I, план 30 км)
    return {
        "days": [
            {"day": 1, "type": "rest"},
            {"day": 2, "type": "easy", "distance_km": 5},
            {"day": 3, "type": "easy", "distance_km": 6},
            {"day": 4, "type": "cross"},
            {"day": 5, "type": "easy", "distance_km": 5},
            {"day": 6, "type": "long", "distance_km": 9},
            {"day": 7, "type": "easy", "distance_km": 5},
        ]
    }


def test_week_in_phase_one_rejects_quality_and_retries():
    macro = MacroPlan.model_validate(macro_data())
    gen, ai = generator([dumps(good_week_data()), dumps(base_week_data())])
    week = asyncio.run(gen.generate_week(PROFILE, "21.1 км", macro, 1, "01.03.2027", "07.03.2027"))
    assert week.total_km == 30.0
    assert len(ai.calls) == 2
    assert "запрещены тренировки типов" in ai.calls[0]["messages"][-1]["content"]
    assert "недопустима в фазе 1" in ai.calls[1]["messages"][-1]["content"]


def test_week_long_run_cap_from_zones():
    # медленному бегуну (VDOT 30) 150 мин хватает примерно на 19 км: длиннее отклоняется, в промпте потолок в км
    zones = calculate_zones(30)
    cap = long_run_max_km(zones)
    macro = MacroPlan.model_validate(macro_data())
    too_long = good_week_data()
    too_long["days"] = [
        {"day": 1, "type": "rest"}, {"day": 2, "type": "easy", "distance_km": 14},
        {"day": 3, "type": "easy", "distance_km": 14}, {"day": 4, "type": "easy", "distance_km": 14},
        {"day": 5, "type": "rest"}, {"day": 6, "type": "long", "distance_km": math.ceil(cap) + 1},
        {"day": 7, "type": "easy", "distance_km": 14},
    ]
    gen, ai = generator([dumps(too_long), dumps(good_week_data())])
    asyncio.run(gen.generate_week(PROFILE, "21.1 км", macro, 5, "01.03.2027", "07.03.2027", zones=zones))
    assert f"не длиннее {cap:g} км (150 мин в лёгком темпе атлета)" in ai.calls[0]["messages"][-1]["content"]
    assert "дольше потолка 150 мин" in ai.calls[1]["messages"][-1]["content"]


def test_week_prompt_without_zones_has_no_time_cap():
    macro = MacroPlan.model_validate(macro_data())
    gen, ai = generator([dumps(good_week_data())])
    asyncio.run(gen.generate_week(PROFILE, "21.1 км", macro, 5, "01.03.2027", "07.03.2027"))
    assert "мин в лёгком темпе" not in ai.calls[0]["messages"][-1]["content"]


def test_phase_rule_only_in_phase_one_prompt():
    macro = MacroPlan.model_validate(macro_data())
    gen, ai = generator([dumps(good_week_data())])
    asyncio.run(gen.generate_week(PROFILE, "21.1 км", macro, 5, "01.03.2027", "07.03.2027"))
    assert "запрещены тренировки типов" not in ai.calls[0]["messages"][-1]["content"]


def test_macro_prompt_mentions_taper_limit():
    gen, ai = generator([dumps(macro_data())])
    asyncio.run(gen.generate_macro(PROFILE, "21.1 км", "2027-06-15", 12))
    prompt = ai.calls[0]["messages"][-1]["content"]
    assert "75% от пиковой" in prompt
    assert "не больше предыдущей" in prompt
    assert "в фазе 2 или 3" in prompt
    assert "ниже 70% от первой недели" in prompt
    assert "Названия фаз не указывай" in prompt
    assert '"name"' not in prompt


# ---------- вводные дни до старта плана ----------

def intro_data(first_type="easy"):
    first = {"day": 4, "type": first_type, "distance_km": 6}
    if first_type == "threshold":
        first["quality_km"] = 2
    return {"days": [
        {"day": 1, "type": "rest"}, {"day": 2, "type": "rest"}, {"day": 3, "type": "rest"},
        first,
        {"day": 5, "type": "easy", "distance_km": 5},
        {"day": 6, "type": "long", "distance_km": 9},
        {"day": 7, "type": "rest"},
    ]}


INTRO_DAYS = [date(2026, 10, 8), date(2026, 10, 9), date(2026, 10, 10), date(2026, 10, 11)]   # чт–вс


def test_intro_target_and_long_cap():
    assert intro_target_km(37.8, 4) == 21.6
    assert intro_long_cap_km(37.8, None) == 11.3                        # 30% обычной недели
    assert intro_long_cap_km(80, calculate_zones(30)) == long_run_max_km(calculate_zones(30))  # 150 мин раньше 30%


def test_intro_days_prompt_and_retry_on_quality():
    gen, ai = generator([dumps(intro_data("threshold")), dumps(intro_data())])
    week = asyncio.run(gen.generate_intro_days(PROFILE, "21.1 км", INTRO_DAYS, weekly_km=37.8))
    assert week.total_km == 20.0
    prompt = ai.calls[0]["messages"][-1]["content"]
    assert "Чт 08.10, Пт 09.10, Сб 10.10, Вс 11.10 (4 дн.)" in prompt
    assert "Объём вводных дней: 21.6 км" in prompt
    assert "Дни Пн, Вт, Ср уже прошли" in prompt
    assert "не длиннее 11.3 км" in prompt
    assert ai.calls[0]["response_schema"] is WeekPlan
    assert "недопустима во вводные дни" in ai.calls[1]["messages"][-1]["content"]


def test_macro_prompt_includes_goal_only_when_given():
    gen, ai = generator([dumps(macro_data()), dumps(macro_data())])
    asyncio.run(gen.generate_macro(PROFILE, "21.1 км", "2027-06-15", 12))
    asyncio.run(gen.generate_macro(PROFILE, "21.1 км", "2027-06-15", 12, goal_text="Целевое время: 1:45:00"))
    without, with_goal = (c["messages"][-1]["content"] for c in ai.calls)
    assert "Целевое время" not in without
    assert "Целевое время: 1:45:00" in with_goal

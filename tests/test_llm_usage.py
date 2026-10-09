import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from bot.access_panel import format_tokens, render_users_panel, sum_totals, usage_text
from bot.middlewares import LlmUsageMiddleware
from clients import llm_usage
from clients.llm_usage import (
    UsageRecord,
    current_scope,
    report_usage,
    set_usage_sink,
    usage_from_anthropic,
    usage_from_openai,
    usage_scope,
)
from models import AppUser
from models.user import ACCESS_APPROVED
from repositories.usage_repo import UsageTotals
from services.coach_qa import ask_limit_text, questions_left_note
from services.user_time import local_day_start, local_month_start

RECORD = UsageRecord("claude-haiku-5-5", 100, 50, 6000, 0)


def _collect(scenario):
    """Запускает scenario с sink, который собирает (scope, record)."""
    saved = []

    async def sink(scope, record):
        saved.append((scope, record))

    async def run():
        set_usage_sink(sink)
        try:
            await scenario()
            await llm_usage.drain()
        finally:
            set_usage_sink(None)

    asyncio.run(run())
    return saved


def test_report_inside_scope_goes_to_sink_with_one_request_id():
    async def scenario():
        with usage_scope(42, "/ask") as scope:
            assert current_scope() is scope
            report_usage(RECORD)          # шаг поиска
            report_usage(RECORD)          # ответ
        assert current_scope() is None
        with usage_scope(42, "/ask"):
            report_usage(RECORD)          # следующий вопрос

    saved = _collect(scenario)
    assert [(s.chat_id, s.command) for s, _ in saved] == [(42, "/ask")] * 3
    first, second, third = (s.request_id for s, _ in saved)
    assert first == second != third       # один вопрос = один запрос


def test_report_outside_scope_or_without_sink_is_ignored():
    async def scenario():
        report_usage(RECORD)

    assert _collect(scenario) == []

    async def without_sink():
        with usage_scope(1, "/ask"):
            report_usage(RECORD)

    asyncio.run(without_sink())          # без sink и без исключений


def test_sink_failure_does_not_break_caller():
    async def run():
        set_usage_sink(AsyncMock(side_effect=RuntimeError("БД недоступна")))
        try:
            with usage_scope(1, "/ask"):
                report_usage(RECORD)
            await llm_usage.drain()
        finally:
            set_usage_sink(None)

    asyncio.run(run())


def test_usage_from_anthropic():
    response = SimpleNamespace(model="claude-haiku-5-5", usage=SimpleNamespace(
        input_tokens=120, output_tokens=800, cache_read_input_tokens=6000, cache_creation_input_tokens=None,
    ))
    assert usage_from_anthropic(response) == UsageRecord("claude-haiku-5-5", 120, 800, 6000, 0)


def test_usage_from_openai_separates_cached_tokens():
    response = SimpleNamespace(model="openai/gpt-oss-20b", usage=SimpleNamespace(
        prompt_tokens=5000, completion_tokens=900, prompt_tokens_details=SimpleNamespace(cached_tokens=3000),
    ))
    assert usage_from_openai(response) == UsageRecord("openai/gpt-oss-20b", 2000, 900, 3000, 0)
    bare = SimpleNamespace(model=None, usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5))
    assert usage_from_openai(bare) == UsageRecord("groq", 10, 5, 0, 0)
    assert usage_from_openai(SimpleNamespace(model="m", usage=None)) == UsageRecord("m", 0, 0, 0, 0)


def test_middleware_sets_scope_by_llm_flag():
    seen = []

    async def handler(event, data):
        seen.append(current_scope())

    event = SimpleNamespace(chat=SimpleNamespace(id=7))
    with_flag = {"handler": SimpleNamespace(flags={"llm": "/analyze"})}
    without_flag = {"handler": SimpleNamespace(flags={})}
    middleware = LlmUsageMiddleware()
    asyncio.run(middleware(handler, event, with_flag))
    asyncio.run(middleware(handler, event, without_flag))
    assert (seen[0].chat_id, seen[0].command) == (7, "/analyze")
    assert seen[1] is None


def test_day_and_month_start_in_user_timezone():
    tz = ZoneInfo("Asia/Almaty")
    now = datetime(2026, 10, 31, 20, 30, tzinfo=timezone.utc)   # в Алматы уже 1 ноября, 01:30
    assert local_day_start(tz, now) == datetime(2026, 11, 1, tzinfo=tz)
    assert local_month_start(tz, now) == datetime(2026, 11, 1, tzinfo=tz)
    assert local_month_start(timezone.utc, now) == datetime(2026, 10, 1, tzinfo=timezone.utc)


def test_ask_limit_texts():
    assert "после полуночи" in ask_limit_text()
    assert questions_left_note(5) is None
    assert questions_left_note(2) == "ℹ️ Осталось вопросов на сегодня: 2."
    assert "последний" in questions_left_note(0)


def test_format_tokens():
    assert format_tokens(950) == "950"
    assert format_tokens(12_345) == "12.3 тыс."
    assert format_tokens(2_500_000) == "2.5 млн"


def test_users_panel_shows_monthly_usage():
    def user(chat_id, name):
        return AppUser(telegram_chat_id=chat_id, access=ACCESS_APPROVED, first_name=name,
                       created_at=datetime(2026, 10, 1, tzinfo=timezone.utc))

    usage = {2: UsageTotals(45_000, 120_000, 6_100, 12), 3: UsageTotals(1_000, 0, 500, 1)}
    text, _ = render_users_panel([user(1, "Админ"), user(2, "Друг"), user(3, "Гость")], {1}, usage, date(2026, 10, 1))
    assert "🪙 Расход ИИ с 01.10: 13 запр. · вход 46.0 тыс., кэш 120.0 тыс. · выход 6.6 тыс." in text
    assert "   🪙 12 запр. · вход 45.0 тыс., кэш 120.0 тыс. · выход 6.1 тыс." in text
    assert "   🪙 1 запр. · вход 1.0 тыс. · выход 500" in text      # без кэша кэш не пишется
    assert text.count("🪙") == 3                                     # у админа без расхода строки нет

    empty, _ = render_users_panel([user(1, "Админ")], {1}, {}, date(2026, 10, 1))
    assert "Расход ИИ с 01.10: нет" in empty
    assert sum_totals([]) == UsageTotals(0, 0, 0, 0)
    assert usage_text(UsageTotals(0, 0, 0, 0)) == "0 запр. · вход 0 · выход 0"

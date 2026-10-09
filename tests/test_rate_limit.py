import pytest

from clients.rate_limit import (
    MIN_COMPLETION_TOKENS,
    RATE_LIMIT_DEFAULT_WAIT,
    RATE_LIMIT_PAD,
    estimate_prompt_tokens,
    fit_max_tokens,
    retry_delay,
)

GROQ_TPM = (
    "Rate limit reached for model `openai/gpt-oss-20b` ... on tokens per minute (TPM): "
    "Limit 8000, Used 3266, Requested 5135. Please try again in 3.0075s. Need more tokens?"
)


def test_header_has_priority():
    assert retry_delay({"retry-after": "4"}, GROQ_TPM) == pytest.approx(4 + RATE_LIMIT_PAD)


def test_seconds_from_message():
    assert retry_delay({}, GROQ_TPM) == pytest.approx(3.0075 + RATE_LIMIT_PAD)


def test_minutes_and_seconds_from_message():
    assert retry_delay(None, "Please try again in 0m42.5s.") == pytest.approx(42.5 + RATE_LIMIT_PAD)


def test_default_when_unknown():
    assert retry_delay({"retry-after": "скоро"}, "лимит") == pytest.approx(RATE_LIMIT_DEFAULT_WAIT + RATE_LIMIT_PAD)


def test_too_long_wait_is_not_retried():
    # дневной лимит токенов: ждать минуты и часы в чате бессмысленно
    assert retry_delay(None, "Please try again in 12m7.2s.") is None
    assert retry_delay({"retry-after": "3600"}, "") is None


def _messages(chars: int, count: int = 2):
    return [{"role": "user", "content": "я" * (chars // count)} for _ in range(count)]


def test_estimate_prompt_tokens():
    # Живая проверка: неделя с каталогом 8377 символов = 3131 токен Groq; оценка того же порядка
    assert 2500 <= estimate_prompt_tokens(_messages(8377)) <= 3200


def test_fit_max_tokens_keeps_request_within_limit():
    first = _messages(8377)                       # первая попытка недели: max_tokens почти не режется
    retry = _messages(9393, count=6)              # третья попытка с прошлыми ответами и замечаниями
    assert fit_max_tokens(first, 6000, 8000) + estimate_prompt_tokens(first) <= 8000
    assert fit_max_tokens(retry, 6000, 8000) < fit_max_tokens(first, 6000, 8000)
    assert fit_max_tokens(retry, 6000, 8000) + estimate_prompt_tokens(retry) <= 8000


def test_fit_max_tokens_bounds():
    assert fit_max_tokens(_messages(300), 3000, 8000) == 3000           # запрошенного хватает: не трогаем
    assert fit_max_tokens(_messages(30000), 6000, 8000) == MIN_COMPLETION_TOKENS
    assert fit_max_tokens(_messages(30000), 6000, 0) == 6000            # 0: лимит не задан

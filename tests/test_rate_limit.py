import pytest

from clients.rate_limit import RATE_LIMIT_DEFAULT_WAIT, RATE_LIMIT_PAD, retry_delay

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

# clients/rate_limit.py
"""Лимиты Groq: сколько ждать после 429 и сколько max_tokens помещается в лимит токенов в минуту. Без I/O."""
import re
from typing import Mapping, Optional, Sequence

RATE_LIMIT_RETRIES = 3          # сколько раз повторять запрос после 429
RATE_LIMIT_MAX_WAIT = 60.0      # дольше не ждём: это уже не минутный, а дневной лимит
RATE_LIMIT_DEFAULT_WAIT = 10.0  # если Groq не сообщил, сколько ждать
RATE_LIMIT_PAD = 0.5            # запас поверх названного времени

# Groq пишет в тексте ошибки: "Please try again in 7.4175s" или "try again in 1m2.5s"
_TRY_AGAIN_RE = re.compile(r"try again in (?:(\d+)m)?(\d+(?:\.\d+)?)s", re.IGNORECASE)


def retry_delay(headers: Optional[Mapping[str, str]], message: str) -> Optional[float]:
    """Секунды до повтора или None, если ждать слишком долго (повторять не нужно)."""
    delay: Optional[float] = None
    value = (headers or {}).get("retry-after")
    if value:
        try:
            delay = float(value)
        except ValueError:
            delay = None
    if delay is None:
        match = _TRY_AGAIN_RE.search(message or "")
        if match:
            delay = int(match.group(1) or 0) * 60 + float(match.group(2))
    if delay is None:
        delay = RATE_LIMIT_DEFAULT_WAIT
    delay += RATE_LIMIT_PAD
    return delay if delay <= RATE_LIMIT_MAX_WAIT else None


# Groq до ответа оценивает запрос как «промпт + max_tokens» и отклоняет (413) всё, что больше лимита
# токенов в минуту (бесплатный тариф 8000). Повтор генератора с прошлым ответом и замечаниями длиннее
# первого запроса и упирается в лимит, если max_tokens не уменьшить.
CHARS_PER_TOKEN_ESTIMATE = 3.0   # с запасом: русский текст в токенизаторе gpt-oss около 2.5–4 символов
MESSAGE_OVERHEAD_TOKENS = 20
MIN_COMPLETION_TOKENS = 1500     # меньше модели с рассуждениями не хватит на ответ


def estimate_prompt_tokens(messages: Sequence[Mapping[str, str]]) -> int:
    chars = sum(len(m.get("content") or "") for m in messages)
    return int(chars / CHARS_PER_TOKEN_ESTIMATE) + MESSAGE_OVERHEAD_TOKENS * len(messages)


def fit_max_tokens(messages: Sequence[Mapping[str, str]], max_tokens: int, tpm_limit: int) -> int:
    """max_tokens, при котором оценка запроса не больше tpm_limit (0: без лимита). Не меньше MIN_COMPLETION_TOKENS."""
    if not tpm_limit:
        return max_tokens
    room = tpm_limit - estimate_prompt_tokens(messages)
    return max(MIN_COMPLETION_TOKENS, min(max_tokens, room))

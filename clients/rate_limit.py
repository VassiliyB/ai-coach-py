# clients/rate_limit.py
"""Сколько ждать после ответа 429 от Groq. Чистая функция, без I/O."""
import re
from typing import Mapping, Optional

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

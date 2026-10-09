# clients/llm_usage.py
"""Учёт расхода токенов LLM: кто (пользователь) и где (команда) потратил.

Клиенты LLM после ответа вызывают report_usage(...). Чей это вызов, они не знают: пользователя и команду
задаёт usage_scope (contextvars) там, где начинается работа: middleware для хендлеров с флагом llm,
рассылка недель и автоматический разбор пробежек для себя. Вызовы в одном scope составляют один запрос
(request_id): /ask делает два вызова модели, но это один вопрос.
Запись в БД делает sink, который задаёт main.py; без sink и вне scope учёт молча пропускается
(тесты, скрипты). Модуль без I/O и без импорта config.
"""
import asyncio
import logging
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterator, Optional, Set

logger = logging.getLogger(__name__)


# Команды фоновых задач (у хендлеров команда берётся из флага llm)
USAGE_WEEKLY = "рассылка"
USAGE_AUTO_ANALYSIS = "автоанализ"


@dataclass(frozen=True)
class UsageScope:
    chat_id: int
    command: str          # '/ask', '/analyze', '/plan', '/test_week', 'рассылка', 'автоанализ'
    request_id: str


@dataclass(frozen=True)
class UsageRecord:
    model: str
    input_tokens: int            # без кэша
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


def usage_from_anthropic(response: Any) -> UsageRecord:
    """Расход из ответа Messages API: input_tokens уже без кэша, чтение и запись кэша отдельно."""
    usage = response.usage
    return UsageRecord(
        model=getattr(response, "model", None) or "claude",
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
    )


def usage_from_openai(response: Any) -> UsageRecord:
    """Расход из ответа OpenAI-совместимого API (Groq): кэшированные токены входа отдельно, если они есть."""
    usage = getattr(response, "usage", None)
    prompt = getattr(usage, "prompt_tokens", 0) or 0
    cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0
    return UsageRecord(
        model=getattr(response, "model", None) or "groq",
        input_tokens=max(0, prompt - cached),
        output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        cache_read_tokens=cached,
    )


UsageSink = Callable[[UsageScope, UsageRecord], Awaitable[None]]

_scope: ContextVar[Optional[UsageScope]] = ContextVar("llm_usage_scope", default=None)
_sink: Optional[UsageSink] = None
_tasks: Set[asyncio.Task] = set()     # ссылки на задачи записи: иначе сборщик мусора может их снять


@contextmanager
def usage_scope(chat_id: int, command: str) -> Iterator[UsageScope]:
    """Все вызовы LLM внутри блока записываются на chat_id и command одним запросом."""
    scope = UsageScope(chat_id, command, uuid.uuid4().hex)
    token = _scope.set(scope)
    try:
        yield scope
    finally:
        _scope.reset(token)


def current_scope() -> Optional[UsageScope]:
    return _scope.get()


def set_usage_sink(sink: Optional[UsageSink]) -> None:
    global _sink
    _sink = sink


def report_usage(record: UsageRecord) -> None:
    """Из клиента LLM: запись уходит в sink фоновой задачей, ответ пользователю не ждёт БД."""
    scope = _scope.get()
    if scope is None or _sink is None:
        return
    try:
        task = asyncio.get_running_loop().create_task(_write(_sink, scope, record))
    except RuntimeError:     # нет работающего loop
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _write(sink: UsageSink, scope: UsageScope, record: UsageRecord) -> None:
    try:
        await sink(scope, record)
    except Exception as exc:   # учёт не должен ломать ответ; WARNING, чтобы не уведомлять админов на каждый вызов
        logger.warning("Расход токенов не записан (chat_id=%s, %s): %s", scope.chat_id, scope.command, exc)


async def drain() -> None:
    """При остановке и в тестах: дождаться записи всех отчётов."""
    if _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)

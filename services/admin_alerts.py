# services/admin_alerts.py
"""Уведомления админам об ошибках: записи лога уровня ERROR и выше уходят в Telegram.

Обработчик висит на корневом логгере, поэтому ловит все logger.exception / logger.error приложения
и aiogram (необработанное исключение хендлера) без правок в местах ошибок.
- Записи копятся FLUSH_DELAY_S секунд и уходят одним сообщением: ошибка клиента LLM и хендлера,
  который её поймал, приходят вместе.
- Одна и та же ошибка (логгер + шаблон сообщения) не чаще раза в THROTTLE_WINDOW: рассылка недель
  повторяет упавшую генерацию каждый час, сбой Garmin или LLM бьёт по всем пользователям сразу.
  Подавленные повторы считаются и упоминаются в следующем уведомлении о той же ошибке.
- Сбой отправки не логируется на уровне ERROR: иначе уведомление о сбое уведомления по кругу.
"""
import asyncio
import html
import logging
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

FLUSH_DELAY_S = 5.0
THROTTLE_WINDOW = timedelta(hours=1)
MAX_MESSAGE_CHARS = 400         # текст записи лога
MAX_TRACEBACK_CHARS = 1500      # хвост трассировки: там место ошибки; только у первой записи с трассировкой
MAX_ENTRIES_PER_ALERT = 5       # остальные записи пачки только числом
TELEGRAM_LIMIT = 4000           # лимит сообщения 4096 с запасом

# Записи, о которых сообщать бессмысленно: Telegram недоступен, доставить всё равно нельзя
IGNORED = (("aiogram.dispatcher", "Failed to fetch updates"),)

AlertKey = Tuple[str, str]


@dataclass(frozen=True)
class AlertEntry:
    logger_name: str
    level: str
    template: str            # record.msg без подстановки: ключ для подавления повторов
    message: str
    traceback: Optional[str]
    created: datetime

    @property
    def key(self) -> AlertKey:
        return self.logger_name, self.template


def entry_from_record(record: logging.LogRecord) -> AlertEntry:
    tb = None
    if record.exc_info and record.exc_info[0] is not None:
        tb = "".join(traceback.format_exception(*record.exc_info))
    return AlertEntry(
        logger_name=record.name,
        level=record.levelname,
        template=str(record.msg),
        message=record.getMessage(),
        traceback=tb,
        created=datetime.fromtimestamp(record.created, tz=timezone.utc),
    )


def is_ignored(record: logging.LogRecord) -> bool:
    if record.name == __name__:
        return True
    return any(record.name == name and str(record.msg).startswith(prefix) for name, prefix in IGNORED)


class AlertThrottle:
    """Не чаще раза в window на ключ; считает подавленные повторы."""

    def __init__(self, window: timedelta = THROTTLE_WINDOW) -> None:
        self.window = window
        self._last_sent: Dict[AlertKey, datetime] = {}
        self._suppressed: Dict[AlertKey, int] = {}

    def allow(self, key: AlertKey, now: datetime) -> Tuple[bool, int]:
        """(отправлять ли, сколько повторов подавлено с прошлой отправки)."""
        last = self._last_sent.get(key)
        if last is not None and now - last < self.window:
            self._suppressed[key] = self._suppressed.get(key, 0) + 1
            return False, 0
        self._last_sent[key] = now
        return True, self._suppressed.pop(key, 0)


def _cut(text: str, limit: int, tail: bool = False) -> str:
    if len(text) <= limit:
        return text
    return "…" + text[-limit:] if tail else text[:limit] + "…"


@dataclass(frozen=True)
class AlertBatch:
    """Что отправить: (запись, подавлено повторов) и сколько записей не влезло в пачку."""
    entries: List[Tuple[AlertEntry, int]]
    skipped: int = 0


def format_alert(batch: AlertBatch, tz: tzinfo = timezone.utc, with_traceback: bool = True) -> str:
    """Сообщение админу (HTML), время в его поясе tz (в UTC с пометкой «UTC»).

    Трассировка только у первой записи, где она есть: обычно это исходная ошибка, остальные её следствия.
    Не влезает в лимит Telegram: сообщение без трассировки.
    """
    lines = ["⚠️ <b>Ошибка в боте</b>"]
    traceback_shown = not with_traceback
    utc_mark = " UTC" if tz is timezone.utc else ""
    for entry, suppressed in batch.entries:
        lines.append("")
        lines.append(f"<b>{html.escape(entry.logger_name)}</b> · {entry.created.astimezone(tz):%d.%m %H:%M}{utc_mark}")
        lines.append(html.escape(_cut(entry.message, MAX_MESSAGE_CHARS)))
        if suppressed:
            lines.append(f"<i>Ещё {suppressed} раз(а) за последний час, о них не сообщалось.</i>")
        if entry.traceback and not traceback_shown:
            traceback_shown = True
            lines.append(f"<pre>{html.escape(_cut(entry.traceback.strip(), MAX_TRACEBACK_CHARS, tail=True))}</pre>")
    if batch.skipped:
        lines.append(f"\n<i>И ещё {batch.skipped} записей, подробности в логах.</i>")
    text = "\n".join(lines)
    if len(text) > TELEGRAM_LIMIT and with_traceback:
        return format_alert(batch, tz, with_traceback=False)
    return text


def select_alert(
    entries: Iterable[AlertEntry], throttle: AlertThrottle, now: datetime,
) -> Optional[AlertBatch]:
    """Пачка записей -> что отправить, или None, если все записи подавлены как повторы."""
    allowed: List[Tuple[AlertEntry, int]] = []
    for entry in entries:
        ok, suppressed = throttle.allow(entry.key, now)
        if ok:
            allowed.append((entry, suppressed))
    if not allowed:
        return None
    return AlertBatch(allowed[:MAX_ENTRIES_PER_ALERT], skipped=max(0, len(allowed) - MAX_ENTRIES_PER_ALERT))


class AdminAlertHandler(logging.Handler):
    """Пересылает записи ERROR+ через send(batch): форматирует send, у каждого админа свой пояс.
    Работает в event loop бота; emit можно звать из потоков (Garmin вызывается через asyncio.to_thread)."""

    def __init__(
        self,
        send: Callable[[AlertBatch], Awaitable[None]],
        loop: asyncio.AbstractEventLoop,
        delay_s: float = FLUSH_DELAY_S,
        throttle: Optional[AlertThrottle] = None,
    ) -> None:
        super().__init__(level=logging.ERROR)
        self._send = send
        self._loop = loop
        self._delay_s = delay_s
        self._throttle = throttle or AlertThrottle()
        self._pending: List[AlertEntry] = []
        self._flush_task: Optional[asyncio.Task] = None

    def emit(self, record: logging.LogRecord) -> None:
        if is_ignored(record):
            return
        try:
            entry = entry_from_record(record)
            self._loop.call_soon_threadsafe(self._add, entry)
        except Exception:   # закрытый loop при остановке и т.п.: лог уже записан другими обработчиками
            self.handleError(record)

    def _add(self, entry: AlertEntry) -> None:
        self._pending.append(entry)
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = self._loop.create_task(self._flush_later())

    async def _flush_later(self) -> None:
        # Записи, пришедшие во время отправки, уходят следующей пачкой той же задачей
        while self._pending:
            await asyncio.sleep(self._delay_s)
            await self.flush_now()

    async def flush_now(self) -> None:
        entries, self._pending = self._pending, []
        if not entries:
            return
        batch = select_alert(entries, self._throttle, datetime.now(timezone.utc))
        if batch is None:
            return
        try:
            await self._send(batch)
        except Exception as exc:
            logger.warning("Не удалось отправить уведомление об ошибке админам: %s", exc)

    async def aclose(self) -> None:
        """При остановке: отправить накопленное, не дожидаясь задержки."""
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
        await self.flush_now()


def install_admin_alerts(
    bot: Any,
    admin_ids: Iterable[int],
    timezone_of: Callable[[int], Awaitable[tzinfo]],
    fallback_tz: tzinfo = timezone.utc,
) -> Optional[AdminAlertHandler]:
    """Вешает обработчик на корневой логгер. None, если админов нет. Вызывать внутри работающего event loop.

    timezone_of(chat_id): пояс админа (его /timezone). Читается при каждой отправке: смена пояса действует
    сразу. Не прочитался (например, упала БД, о чём и уведомление): fallback_tz.
    """
    admins = sorted(admin_ids)
    if not admins:
        return None

    async def send(batch: AlertBatch) -> None:
        for chat_id in admins:
            try:
                tz = await timezone_of(chat_id)
            except Exception as exc:
                logger.warning("Пояс админа %s не прочитан, время в поясе по умолчанию: %s", chat_id, exc)
                tz = fallback_tz
            try:
                await bot.send_message(chat_id=chat_id, text=format_alert(batch, tz), parse_mode="HTML")
            except Exception as exc:   # один недоступный админ не мешает остальным
                logger.warning("Уведомление об ошибке не доставлено админу %s: %s", chat_id, exc)

    handler = AdminAlertHandler(send, asyncio.get_running_loop())
    logging.getLogger().addHandler(handler)
    return handler

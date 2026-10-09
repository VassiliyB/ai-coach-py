import asyncio
import logging
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from services.admin_alerts import (
    MAX_ENTRIES_PER_ALERT,
    TELEGRAM_LIMIT,
    AdminAlertHandler,
    AlertBatch,
    AlertEntry,
    AlertThrottle,
    entry_from_record,
    format_alert,
    install_admin_alerts,
    is_ignored,
    select_alert,
)

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


def _entry(name="services.scheduler_service", template="Ошибка при генерации недели для user_id=%s: %s",
           message="Ошибка при генерации недели для user_id=7: boom", tb=None):
    return AlertEntry(name, "ERROR", template, message, tb, NOW)


def _record(msg, *args, name="services.scheduler_service", exc=False, level=logging.ERROR):
    exc_info = None
    if exc:
        try:
            raise RuntimeError("сломалось")
        except RuntimeError:
            import sys
            exc_info = sys.exc_info()
    return logging.LogRecord(name, level, __file__, 1, msg, args, exc_info)


def test_throttle_once_per_window_and_counts_repeats():
    throttle = AlertThrottle(timedelta(hours=1))
    key = ("x", "шаблон %s")
    assert throttle.allow(key, NOW) == (True, 0)
    assert throttle.allow(key, NOW + timedelta(minutes=10)) == (False, 0)
    assert throttle.allow(key, NOW + timedelta(minutes=50)) == (False, 0)
    # Через час снова, с числом подавленных повторов
    assert throttle.allow(key, NOW + timedelta(minutes=61)) == (True, 2)
    assert throttle.allow(key, NOW + timedelta(hours=3)) == (True, 0)
    # Другой ключ не подавляется
    assert throttle.allow(("x", "другой"), NOW + timedelta(minutes=62)) == (True, 0)


def test_entry_from_record_keeps_template_and_traceback():
    entry = entry_from_record(_record("Ошибка для user_id=%s", 7, exc=True))
    assert entry.template == "Ошибка для user_id=%s"
    assert entry.message == "Ошибка для user_id=7"
    assert "RuntimeError: сломалось" in entry.traceback
    assert entry.key == ("services.scheduler_service", "Ошибка для user_id=%s")
    assert entry_from_record(_record("без трассировки")).traceback is None


def test_ignored_records():
    assert is_ignored(_record("Failed to fetch updates - %s: %s", "A", "b", name="aiogram.dispatcher"))
    assert is_ignored(_record("сбой отправки", name="services.admin_alerts"))
    assert not is_ignored(_record("Cause exception while process update", name="aiogram.event"))


def test_format_escapes_and_shows_suppressed():
    entry = _entry(message="user_id=7: <script>", tb="Traceback\n  File <module>\nValueError: x")
    text = format_alert(AlertBatch([(entry, 3)]))
    assert "&lt;script&gt;" in text and "<script>" not in text
    assert "<pre>Traceback" in text and "&lt;module&gt;" in text
    assert "Ещё 3 раз(а)" in text
    assert "09.10 12:00 UTC" in text


def test_format_traceback_only_for_first_and_fits_limit():
    entries = [(_entry(template=f"t{i}", message="m" * 1000, tb="T" * 5000), 0) for i in range(MAX_ENTRIES_PER_ALERT)]
    text = format_alert(AlertBatch(entries, skipped=2))
    assert text.count("<pre>") == 1
    assert len(text) <= TELEGRAM_LIMIT
    assert "И ещё 2 записей" in text


def test_format_drops_traceback_when_too_long():
    tb = "<" * 1500   # после экранирования &lt; в 4 раза длиннее
    entries = [(_entry(template=f"t{i}", message="m" * 400, tb=tb), 0) for i in range(5)]
    text = format_alert(AlertBatch(entries))
    assert "<pre>" not in text and len(text) <= TELEGRAM_LIMIT


def test_format_in_admin_timezone():
    text = format_alert(AlertBatch([(_entry(), 0)]), ZoneInfo("Asia/Almaty"))
    assert "09.10 17:00" in text and "UTC" not in text      # 12:00 UTC = 17:00 в Алматы


def test_select_alert_skips_fully_suppressed_batch():
    throttle = AlertThrottle()
    assert select_alert([_entry()], throttle, NOW) is not None
    assert select_alert([_entry(), _entry()], throttle, NOW + timedelta(minutes=5)) is None
    batch = select_alert([_entry(), _entry(template="другая", message="другая ошибка")], throttle, NOW)
    assert [e.message for e, _ in batch.entries] == ["другая ошибка"]


def test_handler_batches_records_into_one_message():
    async def scenario():
        send = AsyncMock()
        handler = AdminAlertHandler(send, asyncio.get_running_loop(), delay_s=0.01)
        log = logging.getLogger("test_admin_alerts.batch")
        log.propagate = False
        log.addHandler(handler)
        try:
            log.error("Ошибка Claude API HTTP %s: %s", 500, "x")
            try:
                raise RuntimeError("сломалось")
            except RuntimeError:
                log.exception("Ошибка при генерации недели для user_id=%s", 7)
            log.warning("предупреждение")     # ниже ERROR: уровень обработчика отсекает
            await asyncio.sleep(0.05)
        finally:
            log.removeHandler(handler)
        return send

    send = asyncio.run(scenario())
    send.assert_awaited_once()
    # Предупреждение ниже ERROR в пачку не попало
    messages = [e.message for e, _ in send.await_args.args[0].entries]
    assert messages == ["Ошибка Claude API HTTP 500: x", "Ошибка при генерации недели для user_id=7"]


def test_handler_accepts_records_from_threads():
    async def scenario():
        send = AsyncMock()
        handler = AdminAlertHandler(send, asyncio.get_running_loop(), delay_s=0.01)
        thread = threading.Thread(target=handler.handle, args=(_record("Ошибка в потоке Garmin"),))
        thread.start()
        await asyncio.to_thread(thread.join)
        await asyncio.sleep(0.05)
        return send

    send = asyncio.run(scenario())
    assert send.await_args.args[0].entries[0][0].message == "Ошибка в потоке Garmin"


def test_handler_survives_send_failure_and_aclose_flushes():
    async def scenario():
        send = AsyncMock(side_effect=RuntimeError("Telegram недоступен"))
        handler = AdminAlertHandler(send, asyncio.get_running_loop(), delay_s=10)
        handler.handle(_record("ошибка перед остановкой"))
        await asyncio.sleep(0)            # call_soon_threadsafe -> _add
        await handler.aclose()            # не ждёт 10 с и не падает от сбоя отправки
        return send

    send = asyncio.run(scenario())
    send.assert_awaited_once()


def test_install_sends_to_every_admin_in_own_timezone():
    async def timezone_of(chat_id):
        if chat_id == 10:
            return ZoneInfo("Asia/Almaty")
        raise RuntimeError("БД недоступна")

    async def scenario():
        bot = AsyncMock()
        bot.send_message.side_effect = [RuntimeError("бот заблокирован"), None, None]
        assert install_admin_alerts(bot, [], timezone_of) is None
        handler = install_admin_alerts(bot, [20, 10, 30], timezone_of, fallback_tz=ZoneInfo("Europe/Moscow"))
        try:
            await handler._send(AlertBatch([(_entry(), 0)]))
        finally:
            logging.getLogger().removeHandler(handler)
        return bot

    calls = asyncio.run(scenario()).send_message.await_args_list
    # Сбой отправки первому админу не мешает остальным; у каждого своё время
    assert [c.kwargs["chat_id"] for c in calls] == [10, 20, 30]
    assert "09.10 17:00" in calls[0].kwargs["text"]                  # пояс админа
    assert all("09.10 15:00" in c.kwargs["text"] for c in calls[1:])  # не прочитан: пояс по умолчанию

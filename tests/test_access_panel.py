from datetime import datetime, timezone

from bot.access_panel import (
    BUTTON_NAME_LIMIT,
    PANEL_APPROVE_PREFIX,
    PANEL_BLOCK_PREFIX,
    button_name,
    render_users_panel,
)
from models import AppUser
from models.user import ACCESS_APPROVED, ACCESS_BLOCKED, ACCESS_PENDING

ADMIN = 1


def _user(chat_id: int, access: str, first_name: str | None = "Имя", username: str | None = None) -> AppUser:
    return AppUser(
        telegram_chat_id=chat_id, access=access, first_name=first_name, username=username,
        created_at=datetime(2026, 10, 9, tzinfo=timezone.utc),
    )


def _callbacks(markup) -> list[list[str]]:
    return [[b.callback_data for b in row] for row in markup.inline_keyboard]


def test_buttons_depend_on_status_and_admin_has_none():
    users = [
        _user(ADMIN, ACCESS_APPROVED, "Админ"),
        _user(2, ACCESS_PENDING, "Новый"),
        _user(3, ACCESS_APPROVED, "Друг"),
        _user(4, ACCESS_BLOCKED, "Чужой"),
    ]
    text, markup = render_users_panel(users, {ADMIN})

    # Порядок групп: ждут одобрения, с доступом, без доступа
    assert _callbacks(markup) == [
        [f"{PANEL_APPROVE_PREFIX}2", f"{PANEL_BLOCK_PREFIX}2"],
        [f"{PANEL_BLOCK_PREFIX}3"],
        [f"{PANEL_APPROVE_PREFIX}4"],
    ]
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert labels == ["✅ Пустить: Новый", "⛔ Отказать", "⛔ Закрыть доступ: Друг", "✅ Вернуть доступ: Чужой"]
    assert "(админ)" in text
    assert "Как управлять доступом" in text
    assert text.index("Ждут одобрения") < text.index("С доступом") < text.index("Без доступа")


def test_only_admin_gives_no_keyboard():
    text, markup = render_users_panel([_user(ADMIN, ACCESS_APPROVED)], {ADMIN})
    assert markup is None
    assert "Как пустить человека" in text and "/start" in text   # без кнопок панель всё равно объясняет, что делать


def test_empty_list():
    text, markup = render_users_panel([], {ADMIN})
    assert markup is None
    assert text == "Пользователей пока нет."


def test_names_are_escaped_in_text_but_plain_on_buttons():
    text, markup = render_users_panel([_user(2, ACCESS_PENDING, "<b>Вася</b>", "va_sya")], {ADMIN})
    assert "&lt;b&gt;Вася&lt;/b&gt;" in text and "@va_sya" in text
    assert markup.inline_keyboard[0][0].text == "✅ Пустить: <b>Вася</b>"   # кнопки HTML не разбирают


def test_button_name_fallbacks_and_limit():
    assert button_name(_user(2, ACCESS_PENDING, None, "runner")) == "@runner"
    assert button_name(_user(2, ACCESS_PENDING, None, None)) == "2"
    long_name = button_name(_user(2, ACCESS_PENDING, "А" * 40))
    assert len(long_name) == BUTTON_NAME_LIMIT and long_name.endswith("…")


def test_callback_data_fits_telegram_limit():
    _, markup = render_users_panel([_user(-1001234567890123, ACCESS_PENDING)], {ADMIN})
    assert all(len(cb.encode()) <= 64 for row in _callbacks(markup) for cb in row)

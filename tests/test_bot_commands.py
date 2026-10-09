import asyncio
import re
from pathlib import Path
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SetMyCommands
from aiogram.types import BotCommandScopeAllPrivateChats, BotCommandScopeChat

from bot.commands import ADMIN_COMMANDS, USER_COMMANDS, commands_help, setup_bot_commands

HANDLERS_DIR = Path(__file__).resolve().parent.parent / "bot" / "handlers"


def _handled_commands() -> set[str]:
    """Команды из Command("...") в хендлерах; /start ловит CommandStart (хендлеры тянут БД, поэтому по тексту)."""
    source = "\n".join(p.read_text(encoding="utf-8") for p in HANDLERS_DIR.glob("*.py"))
    found = set(re.findall(r'Command\("([a-z_]+)"\)', source))
    if "CommandStart()" in source:
        found.add("start")
    return found


def test_every_menu_command_has_handler():
    menu = {name for name, _ in USER_COMMANDS + ADMIN_COMMANDS}
    assert menu <= _handled_commands()


def test_commands_follow_telegram_rules():
    names = [name for name, _ in USER_COMMANDS + ADMIN_COMMANDS]
    assert len(names) == len(set(names))
    for name, description in USER_COMMANDS + ADMIN_COMMANDS:
        assert re.fullmatch(r"[a-z0-9_]{1,32}", name)
        assert 1 <= len(description) <= 256


def test_admin_commands_not_in_common_menu():
    common = {name for name, _ in USER_COMMANDS}
    assert {"users", "approve", "revoke", "test_week"}.isdisjoint(common)
    assert {"approve", "revoke"}.isdisjoint(name for name, _ in ADMIN_COMMANDS)   # без chat_id бесполезны


def test_help_lists_commands_without_start():
    text = commands_help()
    assert "/start" not in text
    assert "<code>/sync</code> — обновить спортивный паспорт" in text
    assert text.count("•") == len(USER_COMMANDS) - 1


def test_setup_sets_common_and_admin_menus():
    bot = AsyncMock()
    asyncio.run(setup_bot_commands(bot, [10, 20]))

    calls = bot.set_my_commands.await_args_list
    assert isinstance(calls[0].kwargs["scope"], BotCommandScopeAllPrivateChats)
    assert len(calls[0].args[0]) == len(USER_COMMANDS)
    admin_calls = calls[1:]
    assert [c.kwargs["scope"].chat_id for c in admin_calls] == [10, 20]
    assert all(isinstance(c.kwargs["scope"], BotCommandScopeChat) for c in admin_calls)
    assert all(len(c.args[0]) == len(USER_COMMANDS) + len(ADMIN_COMMANDS) for c in admin_calls)


def test_setup_survives_unknown_admin_chat():
    bot = AsyncMock()
    error = TelegramBadRequest(method=SetMyCommands(commands=[]), message="chat not found")
    bot.set_my_commands.side_effect = [None, error, None]   # общее меню, админ 10 не писал боту, админ 20
    asyncio.run(setup_bot_commands(bot, [10, 20]))
    assert bot.set_my_commands.await_count == 3

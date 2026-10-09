# bot/handlers/settings.py
import html
import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards import DELETE_CANCEL, DELETE_CONFIRM, get_delete_confirm_keyboard
from clients.garmin import GarminClient
from database import async_session_maker
from services.access_control import AccessControl
from services.heart_rate import (
    KIND_LTHR,
    KIND_MAX,
    check_pulse,
    parse_pulse,
    profile_hr_basis,
    pulse_help,
    render_pulse,
)
from services.user_locks import UserLocks
from services.user_service import UserService
from services.user_time import local_now, normalize_timezone

logger = logging.getLogger(__name__)
router = Router()

TIMEZONE_HELP = (
    "Укажите пояс названием или смещением от UTC, например:\n"
    "• <code>/timezone Asia/Almaty</code>\n"
    "• <code>/timezone Europe/Moscow</code>\n"
    "• <code>/timezone +5</code> или <code>/timezone UTC+05:30</code>\n\n"
    "Название учитывает переход на летнее время, смещение нет."
)


@router.message(Command("timezone"))
async def handle_timezone(message: Message, command: CommandObject) -> None:
    """Просмотр и смена часового пояса: от него зависят даты недель и время воскресной рассылки."""
    async with async_session_maker() as session:
        user = await UserService.get_or_create_user(session, message.chat.id)

        if not command.args:
            current = UserService.timezone_of(user)
            source = html.escape(user.timezone) if user.timezone else "по умолчанию"
            await message.answer(
                f"🕒 Ваш часовой пояс: <b>{source}</b>, сейчас у вас "
                f"<b>{local_now(current):%H:%M %d.%m}</b>.\n"
                "Расписание на неделю приходит в воскресенье после 15:00 по этому времени.\n\n"
                f"{TIMEZONE_HELP}",
                parse_mode="HTML",
            )
            return

        tz = normalize_timezone(command.args)
        if tz is None:
            await message.answer(
                f"❌ Не удалось распознать пояс «{html.escape(command.args.strip()[:50])}».\n\n{TIMEZONE_HELP}",
                parse_mode="HTML",
            )
            return

        await UserService.set_timezone(session, user.id, tz)
        user.timezone = tz

    await message.answer(
        f"✅ Часовой пояс: <b>{html.escape(tz)}</b>, сейчас у вас "
        f"<b>{local_now(UserService.timezone_of(user)):%H:%M %d.%m}</b>.",
        parse_mode="HTML",
    )


@router.message(Command("pulse"))
async def handle_pulse(message: Message, command: CommandObject) -> None:
    """Просмотр пульсовых зон и ручной ввод ЧССmax или пульса ПАНО (services.heart_rate)."""
    async with async_session_maker() as session:
        user = await UserService.get_or_create_user(session, message.chat.id)
        profile = await UserService.get_athlete_profile(session, user.id)
        manual_max, lthr = getattr(profile, "manual_max_hr", None), getattr(profile, "lthr", None)

        if not command.args:
            await message.answer(render_pulse(profile_hr_basis(profile), manual_max, lthr), parse_mode="HTML")
            return

        value = parse_pulse(command.args)
        if value is None:
            await message.answer(
                f"❌ Не удалось распознать «{html.escape(command.args.strip()[:30])}».\n\n"
                f"{pulse_help(manual_max, lthr)}",
                parse_mode="HTML",
            )
            return
        error = check_pulse(value, manual_max, lthr)
        if error:
            await message.answer(f"❌ {error}", parse_mode="HTML")
            return

        if value.kind == KIND_MAX:
            manual_max = value.bpm
        elif value.kind == KIND_LTHR:
            lthr = value.bpm
        else:
            manual_max = lthr = None
        profile = await UserService.set_pulse(session, user.id, manual_max, lthr)

    logger.info("Пульс задан вручную (chat_id=%s): ЧССmax=%s, ПАНО=%s", message.chat.id, manual_max, lthr)
    head = "✅ Ручные значения сброшены." if value.kind is None else "✅ Сохранено."
    await message.answer(
        f"{head} Новые зоны действуют в следующих расписаниях, разборе тренировок и выгрузке в Garmin.\n\n"
        f"{render_pulse(profile_hr_basis(profile), manual_max, lthr, with_help=False)}",
        parse_mode="HTML",
    )


DELETE_WARNING = (
    "🗑 <b>Удаление всех ваших данных</b>\n\n"
    "Будет удалено безвозвратно:\n"
    "• спортивный паспорт (VDOT, пульс, объёмы);\n"
    "• план подготовки и все недельные расписания;\n"
    "• отметки о разобранных тренировках и часовой пояс;\n"
    "• сохранённые токены входа в Garmin Connect.\n\n"
    "Не затрагивается: ваш аккаунт и тренировки в самом Garmin Connect, "
    "а также эта переписка (бот не может удалить историю чата).\n\n"
    "Удалить?"
)


@router.message(Command("delete_me"))
async def handle_delete_me(message: Message) -> None:
    """Первый шаг: предупреждение и кнопки подтверждения. Само удаление только по кнопке."""
    await message.answer(DELETE_WARNING, reply_markup=get_delete_confirm_keyboard(), parse_mode="HTML")


@router.callback_query(F.data == DELETE_CONFIRM)
async def handle_delete_confirm(
    callback: CallbackQuery, state: FSMContext, garmin: GarminClient, user_locks: UserLocks,
    access: AccessControl,
) -> None:
    """Удаляет пользователя из БД (остальное каскадом), токены Garmin и состояние диалога."""
    chat_id = callback.message.chat.id
    # Пока идёт операция пользователя (например, опрос Garmin), удалять нельзя:
    # она записала бы данные уже удалённому пользователю
    async with user_locks.hold(chat_id, "удаление данных") as acquired:
        if not acquired:
            await callback.answer(
                f"Подождите: ещё выполняется {user_locks.current(chat_id)}. Повторите через минуту.",
                show_alert=True,
            )
            return
        try:
            async with async_session_maker() as session:
                existed = await UserService.delete_user(session, chat_id)
            await garmin.clear_session(chat_id)
            await state.clear()
            if not access.is_admin(chat_id):   # запись о доступе удалена вместе с остальным
                access.revoke(chat_id)
        except Exception:
            logger.exception("Ошибка удаления данных (chat_id=%s)", chat_id)
            await callback.answer("Не удалось удалить данные. Попробуйте ещё раз.", show_alert=True)
            return

    logger.info("Данные пользователя удалены по /delete_me (chat_id=%s, был в БД: %s)", chat_id, existed)
    text = "✅ Все ваши данные удалены." if existed else "✅ Данных о вас уже нет."
    await callback.message.edit_text(f"{text}\nЧтобы начать заново, отправьте /start.")
    await callback.answer()


@router.callback_query(F.data == DELETE_CANCEL)
async def handle_delete_cancel(callback: CallbackQuery) -> None:
    await callback.message.edit_text("Удаление отменено, данные на месте.")
    await callback.answer()

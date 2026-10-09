# bot/handlers/start.py
import html
import json
import logging

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardRemove

from bot.keyboards import get_garmin_auth_keyboard
from bot.states import AuthStates
from clients.garmin import (
    GarminAuthError,
    GarminClient,
    GarminClientError,
    GarminRateLimitError,
)
from database import async_session_maker
from services.user_service import UserService

logger = logging.getLogger(__name__)
router = Router()

MFA_CODE_LENGTHS = (6, 8)


async def _announce_linked(status_msg: Message, message: Message, text: str) -> None:
    """Сообщение об успешном входе вместе со снятием кнопки входа.

    Отредактированное сообщение не может убрать reply-клавиатуру, поэтому статус удаляется
    и отправляется новое сообщение с ReplyKeyboardRemove.
    """
    await status_msg.delete()
    await message.answer(text, reply_markup=ReplyKeyboardRemove(), parse_mode="HTML")


@router.message(CommandStart())
async def handle_start(message: Message, state: FSMContext, garmin: GarminClient) -> None:
    """Обработка команды /start (сбрасывает любое незавершённое состояние)."""
    await state.clear()
    chat_id = message.chat.id
    first_name = html.escape(message.from_user.first_name or "атлет")

    async with async_session_maker() as session:
        user = await UserService.get_or_create_user(
            session=session,
            chat_id=chat_id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
        )

    if garmin.has_saved_tokens(chat_id) and user.garmin_linked:
        await message.answer(
            f"👋 С возвращением, <b>{first_name}</b>!\n\n"
            "Ваш Garmin Connect привязан. Доступные команды:\n"
            "• <code>/sync</code> — обновить спортивный паспорт за 90 дней\n"
            "• <code>/plan</code> — составить макроплан к забегу\n"
            "• <code>/analyze</code> — разобрать последнюю пробежку\n"
            "• <code>/timezone</code> — часовой пояс для расписаний\n"
            "• <code>/delete_me</code> — удалить все свои данные",
            reply_markup=ReplyKeyboardRemove(),   # убирает кнопку входа, оставшуюся с момента подключения
            parse_mode="HTML",
        )
    else:
        await message.answer(
            f"Привет, <b>{first_name}</b>! 🏃‍♂️\n\n"
            "Я — твой персональный ИИ-тренер по бегу.\n\n"
            "Подключи профиль Garmin Connect кнопкой внизу:",
            reply_markup=get_garmin_auth_keyboard(),
            parse_mode="HTML",
        )


@router.message(F.web_app_data, flags={"user_lock": "вход в Garmin"})
async def handle_webapp_data(message: Message, state: FSMContext, garmin: GarminClient) -> None:
    """Шаг 1: приём email и пароля из WebApp."""
    chat_id = message.chat.id

    # Кнопка могла остаться на экране: подключённому пользователю повторный вход в Garmin не нужен
    # (лишний вход рискует блокировкой 429). Если сессия истечёт, бот сам сбросит привязку
    async with async_session_maker() as session:
        user = await UserService.get_or_create_user(session, chat_id)
    if garmin.has_saved_tokens(chat_id) and user.garmin_linked:
        await message.answer(
            "✅ Garmin Connect уже подключён, повторный вход не нужен.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    try:
        data = json.loads(message.web_app_data.data)
        email = str(data.get("email", "")).strip()
        password = str(data.get("password", "")).strip()
    except (json.JSONDecodeError, AttributeError, TypeError):
        await message.answer("❌ Не удалось прочитать данные формы. Попробуйте ещё раз.")
        return

    if not email or not password:
        await message.answer("❌ Email и пароль обязательны.")
        return

    status_msg = await message.answer("🔄 Подключаемся к Garmin...")

    try:
        result = await garmin.login_start(chat_id=chat_id, email=email, password=password)

        if result == "needs_mfa":
            await state.set_state(AuthStates.waiting_for_mfa)
            await status_msg.edit_text(
                "📬 <b>Garmin отправил проверочный код!</b>\n\n"
                "Проверьте письма (и папку «Спам») от Garmin.\n"
                "Введите полученный 6-значный код прямо сюда:",
                parse_mode="HTML",
            )
            return

        async with async_session_maker() as session:
            await UserService.set_garmin_linked(session=session, chat_id=chat_id, linked=True)

        await _announce_linked(
            status_msg, message,
            "✅ <b>Garmin Connect подключен!</b>\n\n"
            "Вызовите <code>/sync</code> для загрузки спортивного паспорта.",
        )

    except GarminRateLimitError as exc:
        await status_msg.edit_text(f"⚠️ {exc}")
    except GarminAuthError:
        await status_msg.edit_text("❌ Неверный логин или пароль Garmin Connect. Попробуйте снова.")
    except GarminClientError as exc:
        logger.error("Ошибка входа в Garmin (chat_id=%s): %s", chat_id, exc)
        await status_msg.edit_text("⚠️ Не удалось подключиться к Garmin. Попробуйте позже.")
    except Exception:
        logger.exception("Непредвиденная ошибка входа (chat_id=%s)", chat_id)
        await status_msg.edit_text("❌ Произошла ошибка. Попробуйте ещё раз через /start.")


# Только текст и не команда: /start, /sync и т.д. не будут приняты за код
@router.message(
    AuthStates.waiting_for_mfa, F.text, ~F.text.startswith("/"),
    flags={"user_lock": "вход в Garmin"},
)
async def handle_mfa_code_entered(message: Message, state: FSMContext, garmin: GarminClient) -> None:
    """Шаг 2: приём кода MFA из чата."""
    chat_id = message.chat.id
    code = message.text.strip().replace(" ", "").replace("-", "")

    if not code.isdigit() or len(code) not in MFA_CODE_LENGTHS:
        await message.answer("❌ Код должен состоять из 6 цифр. Попробуйте ещё раз:")
        return

    status_msg = await message.answer("🔐 Проверяю код...")

    try:
        await garmin.login_complete_mfa(chat_id=chat_id, mfa_code=code)

        async with async_session_maker() as session:
            await UserService.set_garmin_linked(session=session, chat_id=chat_id, linked=True)
        await state.clear()

        await _announce_linked(
            status_msg, message,
            "🎉 <b>Авторизация завершена!</b>\n\n"
            "Напишите <code>/sync</code>, чтобы загрузить спортивный паспорт.",
        )

    except GarminAuthError:
        # В клиенте pending-сессия уже сброшена, поэтому нужен новый вход
        await state.clear()
        await status_msg.edit_text("❌ Код не подошёл или устарел. Начните заново через /start.")
    except GarminClientError as exc:
        logger.error("Ошибка MFA (chat_id=%s): %s", chat_id, exc)
        await state.clear()
        await status_msg.edit_text("❌ Не удалось завершить вход. Начните заново через /start.")
    except Exception:
        logger.exception("Непредвиденная ошибка MFA (chat_id=%s)", chat_id)
        await state.clear()
        await status_msg.edit_text("❌ Произошла ошибка. Начните заново через /start.")


@router.message(AuthStates.waiting_for_mfa)
async def handle_mfa_not_text(message: Message) -> None:
    """Стикер, фото и прочее вместо кода."""
    await message.answer("Пришлите код из письма текстом (6 цифр) или начните заново через /start.")

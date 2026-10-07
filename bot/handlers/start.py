# bot/handlers/start.py
import json
import logging
from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from database import async_session_maker
from services.user_service import UserService
from clients.garmin import GarminClient, GarminAuthError, GarminRateLimitError, GarminClientError
from bot.keyboards import get_garmin_auth_keyboard
from bot.states import AuthStates

logger = logging.getLogger(__name__)
router = Router()
garmin_client = GarminClient()


@router.message(CommandStart())
async def handle_start(message: Message, state: FSMContext) -> None:
    """Обработка команды /start."""
    await state.clear()
    chat_id = message.chat.id
    async with async_session_maker() as session:
        user = await UserService.get_or_create_user(
            session=session,
            chat_id=chat_id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
        )

    has_tokens = garmin_client.has_saved_tokens(chat_id)

    if has_tokens and user.garmin_linked:
        await message.answer(
            f"👋 С возвращением, <b>{message.from_user.first_name}</b>!\n\n"
            "Ваш Garmin Connect успешно привязан. Доступные команды:\n"
            "• <code>/sync</code> — обновить спортивный паспорт за 90 дней\n"
            "• <code>/plan</code> — составить целевой макроплан к забегу\n"
            "• <code>/analyze</code> — разобрать последнюю пробежку",
            parse_mode="HTML",
        )
    else:
        await message.answer(
            f"Привет, <b>{message.from_user.first_name}</b>! 🏃‍♂️\n\n"
            "Я — твой персональный ИИ-тренер по бегу.\n\n"
            "Подключи свой профиль Garmin Connect кнопкой внизу (это безопасно):",
            reply_markup=get_garmin_auth_keyboard(),
            parse_mode="HTML",
        )


@router.message(F.web_app_data)
async def handle_webapp_data(message: Message, state: FSMContext) -> None:
    """Шаг 1: Прием email и пароля из WebApp."""
    chat_id = message.chat.id
    try:
        data = json.loads(message.web_app_data.data)
        email = data.get("email", "").strip()
        password = data.get("password", "").strip()

        if not email or not password:
            await message.answer("❌ Ошибка: email и пароль обязательны.")
            return

        status_msg = await message.answer("🔄 Подключаемся к Garmin SSO...")

        # Запускаем шаг 1
        result = await garmin_client.login_start(chat_id=chat_id, email=email, password=password)

        if result == "needs_mfa":
            # Переводим пользователя в режим ожидания 2FA кода
            await state.set_state(AuthStates.waiting_for_mfa)
            await status_msg.edit_text(
                "📬 <b>Garmin отправил проверочный код!</b>\n\n"
                "Проверьте входящие письма (и папку «Спам») от Garmin.\n"
                "Введите полученный 6-значный код прямо сюда в чат:",
                parse_mode="HTML",
            )
        else:
            # Вход без 2FA
            async with async_session_maker() as session:
                await UserService.set_garmin_linked(session=session, chat_id=chat_id, linked=True)

            await status_msg.edit_text(
                "✅ <b>Garmin Connect успешно подключен!</b>\n\n"
                "Сессия сохранена. Вызовите <code>/sync</code> для загрузки спортивного паспорта.",
                parse_mode="HTML",
            )

    except GarminRateLimitError as exc:
        await message.answer(f"⚠️ {exc}")
    except GarminAuthError:
        await message.answer("❌ Неверный логин или пароль Garmin Connect. Попробуйте снова.")
    except GarminClientError as exc:
        await message.answer(f"⚠️ Ошибка: {exc}")


@router.message(AuthStates.waiting_for_mfa)
async def handle_mfa_code_entered(message: Message, state: FSMContext) -> None:
    """Шаг 2: Прием 6-значного кода MFA из чата Telegram."""
    chat_id = message.chat.id
    code = message.text.strip().replace(" ", "").replace("-", "")

    if not code.isdigit() or len(code) not in [6, 8]:
        await message.answer("❌ Код должен состоять из 6 цифр. Попробуйте ввести еще раз:")
        return

    status_msg = await message.answer("🔐 Проверяю код безопасности...")

    try:
        await garmin_client.login_complete_mfa(chat_id=chat_id, mfa_code=code)
        await state.clear()

        async with async_session_maker() as session:
            await UserService.set_garmin_linked(session=session, chat_id=chat_id, linked=True)

        await status_msg.edit_text(
            "🎉 <b>Авторизация успешно завершена!</b>\n\n"
            "Токены Garmin сохранены. Теперь напишите <code>/sync</code>, чтобы рассчитать спортивный паспорт и VDOT.",
            parse_mode="HTML",
        )

    except GarminAuthError as exc:
        await status_msg.edit_text(f"❌ {exc}\nПопробуйте войти заново через /start.")
        await state.clear()
    except Exception as exc:
        logger.exception("Ошибка при завершении MFA: %s", exc)
        await status_msg.edit_text("❌ Произошла ошибка. Попробуйте повторить вход через /start.")
        await state.clear()
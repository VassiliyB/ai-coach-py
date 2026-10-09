# main.py
import asyncio
import logging
from datetime import tzinfo

from aiogram import Bot, Dispatcher

from bot.commands import setup_bot_commands
from bot.fsm_storage import check_fsm_storage, create_fsm_storage
from bot.handlers import access as access_handlers
from bot.handlers import analyze, ask, garmin_export, plan, race, show_plan, start, sync
from bot.handlers import settings as settings_handlers
from bot.middlewares import AccessMiddleware, UserLockMiddleware
from clients.garmin import GarminClient
from clients.llm import create_llm_client
from config import settings
from database import async_session_maker, engine, run_migrations
from services.access_control import AccessControl
from services.activity_poller import ActivityPoller
from services.admin_alerts import install_admin_alerts
from services.ai_coach_service import AICoachService
from services.plan_generator import PlanGenerator
from services.scheduler_service import TrainingSchedulerService
from services.user_locks import UserLocks
from services.user_service import UserService
from services.user_time import to_tzinfo

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
)
logger = logging.getLogger("app_main")


async def main() -> None:
    logger.info("Запуск Garmin AI Coach...")

    # 1. Схема БД: миграции Alembic до head (на актуальной БД ничего не делают)
    await asyncio.to_thread(run_migrations)
    logger.info("Миграции базы данных применены.")

    # 2. Единые экземпляры сервисов (создаются один раз на всё приложение)
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN.get_secret_value())
    # Ошибки из лога уходят админам в Telegram (с этого момента: раньше бота ещё нет), время в поясе админа
    alerts = None
    if settings.ADMIN_ALERTS:
        async def admin_timezone(chat_id: int) -> tzinfo:
            async with async_session_maker() as session:
                return await UserService.timezone_for_chat(session, chat_id)

        alerts = install_admin_alerts(
            bot, settings.ADMIN_CHAT_IDS, admin_timezone, fallback_tz=to_tzinfo(settings.DEFAULT_TIMEZONE),
        )
    garmin_client = GarminClient()
    ai_client = create_llm_client()  # один клиент (Groq или Claude) на текстовые ответы и планы
    logger.info("Провайдер LLM: %s", settings.LLM_PROVIDER)
    ai_coach = AICoachService(ai_client=ai_client)
    plan_generator = PlanGenerator(ai_client=ai_client)
    # Доступ по одобрению админа: одобренные загружаются в память, чтобы не читать БД на каждое сообщение
    access = AccessControl(settings.ADMIN_CHAT_IDS)
    async with async_session_maker() as session:
        access.load(await UserService.load_approved(session, settings.ADMIN_CHAT_IDS))
    if not settings.ADMIN_CHAT_IDS:
        logger.warning("ADMIN_CHAT_IDS не задан: новых пользователей одобрить некому")
    user_locks = UserLocks()  # одна тяжёлая операция на пользователя: команды, опрос, рассылка
    activity_poller = ActivityPoller(bot=bot, garmin=garmin_client, ai_coach=ai_coach, locks=user_locks)
    scheduler_service = TrainingSchedulerService(
        bot=bot, plan_generator=plan_generator,
        activity_poller=activity_poller, poll_minutes=settings.ACTIVITY_POLL_MINUTES, locks=user_locks,
        garmin=garmin_client,
    )

    # Состояния диалогов: Redis переживает перезапуск (REDIS_URL), без него память процесса
    storage = create_fsm_storage(settings.REDIS_URL.get_secret_value() if settings.REDIS_URL else None)
    await check_fsm_storage(storage)

    # 3. Диспетчер: именованные аргументы становятся зависимостями хендлеров (DI).
    #    Имя аргумента = имя параметра в хендлере (garmin, ai_coach, plan_generator, scheduler_service).
    dp = Dispatcher(
        storage=storage,   # закрывает сам Dispatcher при остановке
        garmin=garmin_client,
        ai_coach=ai_coach,
        plan_generator=plan_generator,
        scheduler_service=scheduler_service,
        user_locks=user_locks,
        access=access,
    )

    # Внешний middleware на все обновления: без доступа событие не доходит до роутеров
    dp.update.outer_middleware(AccessMiddleware(access))

    # Хендлеры с флагом user_lock не запускаются, пока у пользователя идёт другая тяжёлая операция.
    # Внутренний middleware: флаги хендлера видны только после его выбора; применяется ко всем роутерам
    lock_middleware = UserLockMiddleware(user_locks)
    dp.message.middleware(lock_middleware)
    dp.callback_query.middleware(lock_middleware)

    # 4. Роутеры. Порядок важен: команды раньше общих хендлеров состояний.
    dp.include_router(access_handlers.router)   # только для админов (фильтр роутера)
    dp.include_router(start.router)
    dp.include_router(sync.router)
    dp.include_router(analyze.router)
    dp.include_router(ask.router)
    dp.include_router(race.router)
    dp.include_router(garmin_export.router)
    dp.include_router(settings_handlers.router)  # до plan: его хендлер состояния ловит любой текст
    dp.include_router(show_plan.router)
    dp.include_router(plan.router)

    # Меню команд Telegram: общее и расширенное для админов (сбой меню не мешает запуску)
    try:
        await setup_bot_commands(bot, settings.ADMIN_CHAT_IDS)
    except Exception:
        logger.exception("Не удалось задать меню команд")

    # 5. Планировщик
    scheduler_service.start()

    logger.info("Telegram-бот запущен и слушает события...")
    try:
        await dp.start_polling(bot)
    finally:
        scheduler_service.shutdown()
        if alerts is not None:
            await alerts.aclose()       # накопленные ошибки уходят до закрытия сессии бота
            logging.getLogger().removeHandler(alerts)
        await bot.session.close()
        await engine.dispose()          # корректно закрываем пул соединений с БД
        logger.info("Приложение остановлено.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Остановлено пользователем.")

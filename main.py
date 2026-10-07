# main.py
import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from config import settings
from database import init_models
from bot.handlers import start, sync, plan, analyze
from services.scheduler_service import TrainingSchedulerService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
)
logger = logging.getLogger("app_main")


async def main() -> None:
    logger.info("Запуск Garmin AI Coach...")

    # 1. Инициализация таблиц базы данных PostgreSQL
    await init_models()
    logger.info("Таблицы базы данных успешно проверены/инициализированы.")

    # 2. Инициализация бота и диспетчера
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    # 3. Регистрация модульных роутеров
    dp.include_router(start.router)
    dp.include_router(sync.router)
    dp.include_router(plan.router)
    dp.include_router(analyze.router)

    # 4. Инициализация и старт фонового планировщика задач (APScheduler)
    scheduler_service = TrainingSchedulerService(bot=bot)
    scheduler_service.start()

    logger.info("Telegram-бот успешно запущен и слушает события...")
    try:
        await dp.start_polling(bot)
    finally:
        scheduler_service.shutdown()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
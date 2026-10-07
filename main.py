# main.py
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from bot.handlers import analyze, plan, start, sync
from clients.garmin import GarminClient
from config import settings
from database import engine, init_models
from services.ai_coach_service import AICoachService
from services.scheduler_service import TrainingSchedulerService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
)
logger = logging.getLogger("app_main")


async def main() -> None:
    logger.info("Запуск Garmin AI Coach...")

    # 1. Таблицы БД
    await init_models()
    logger.info("Таблицы базы данных проверены/инициализированы.")

    # 2. Единые экземпляры сервисов (создаются один раз на всё приложение)
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN.get_secret_value())
    garmin_client = GarminClient()
    ai_coach = AICoachService()
    scheduler_service = TrainingSchedulerService(bot=bot, ai_coach=ai_coach)

    # 3. Диспетчер: именованные аргументы становятся зависимостями хендлеров (DI).
    #    Имя аргумента = имя параметра в хендлере (garmin, ai_coach, scheduler_service).
    dp = Dispatcher(
        storage=MemoryStorage(),
        garmin=garmin_client,
        ai_coach=ai_coach,
        scheduler_service=scheduler_service,
    )

    # 4. Роутеры. Порядок важен: команды раньше общих хендлеров состояний.
    dp.include_router(start.router)
    dp.include_router(sync.router)
    dp.include_router(analyze.router)
    dp.include_router(plan.router)

    # 5. Планировщик
    scheduler_service.start()

    logger.info("Telegram-бот запущен и слушает события...")
    try:
        await dp.start_polling(bot)
    finally:
        scheduler_service.shutdown()
        await bot.session.close()
        await engine.dispose()          # корректно закрываем пул соединений с БД
        logger.info("Приложение остановлено.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Остановлено пользователем.")
# services/garmin_link.py
"""Сброс привязки Garmin, когда сессия истекла: токены удаляются, флаг garmin_linked снимается.

Без этого /start видел бы старый файл токенов, отвечал «С возвращением» без кнопки входа,
и подключиться заново было бы невозможно.
"""
import logging

from clients.garmin import GarminClient
from database import async_session_maker
from services.user_service import UserService

logger = logging.getLogger(__name__)

RELOGIN_TEXT = (
    "❌ Сессия Garmin истекла. Подключите Garmin заново: отправьте <code>/start</code> и нажмите кнопку входа."
)


async def reset_garmin_link(garmin: GarminClient, chat_id: int) -> None:
    await garmin.clear_session(chat_id)
    async with async_session_maker() as session:
        await UserService.set_garmin_linked(session=session, chat_id=chat_id, linked=False)
    logger.info("Сессия Garmin истекла, привязка сброшена (chat_id=%s)", chat_id)

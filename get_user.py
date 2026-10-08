# get_user.py
import asyncio

from sqlalchemy import select

from database import async_session_maker
from models import AppUser


async def main():
    async with async_session_maker() as session:
        result = await session.execute(select(AppUser))
        users = result.scalars().all()
        if not users:
            print("Пользователей в базе пока нет. Напиши боту /start в Telegram!")
            return

        for user in users:
            print(f" Chat ID: {user.telegram_chat_id} | Имя: {user.first_name} | Привязан: {user.garmin_linked}")


if __name__ == "__main__":
    asyncio.run(main())

# generate_token.py
import asyncio
import getpass
from pathlib import Path

from garminconnect import Garmin

from database import async_session_maker
from services.user_service import UserService

CHAT_ID = 268606405
TOKEN_DIR = Path(".garmin_tokens") / str(CHAT_ID)
TOKEN_DIR.mkdir(parents=True, exist_ok=True)


async def main():
    print(f"=== Генерация токена Garmin для пользователя {CHAT_ID} ===")
    email = input("Введи Email от Garmin Connect: ").strip()
    password = getpass.getpass("Введи Пароль от Garmin Connect (ввод не отображается): ")

    print("\nПодключаемся к Garmin SSO...")
    print("👉 Если Garmin запросит 6-значный код (MFA) — введи его из почты сюда в консоль.\n")

    try:
        # В garminconnect >= 0.3 параметр tokenstore автоматически создает garmin_tokens.json
        client = Garmin(email=email, password=password)
        client.login(tokenstore=str(TOKEN_DIR))

        # Дополнительная проверка сохранения для разных подверсий
        if hasattr(client, "dump"):
            client.dump(str(TOKEN_DIR))
        elif hasattr(client, "garth") and hasattr(client.garth, "dump"):
            client.garth.dump(str(TOKEN_DIR))

        token_file = TOKEN_DIR / "garmin_tokens.json"
        print(f"\n🎉 Токены Garmin успешно сохранены в: {token_file}")

        # Автоматически активируем пользователя в PostgreSQL
        async with async_session_maker() as session:
            await UserService.set_garmin_linked(session, chat_id=CHAT_ID, linked=True)
            print("✅ Статус пользователя в базе данных обновлен (garmin_linked = True)!")

        print("\nТеперь можно запускать 'python main.py' и тестировать команду /sync в Telegram!")

    except Exception as exc:
        err_msg = str(exc)
        print(f"\n❌ Ошибка авторизации: {err_msg}")
        if "429" in err_msg:
            print(
                "\n⚠️ Твой текущий IP временно заблокирован защитой Cloudflare (429).\n"
                "Лайфхак: раздай интернет с телефона (мобильная точка доступа/LTE) на ноутбук на 2 минуты,\n"
                "чтобы сменить внешний IP-адрес, и запусти скрипт заново."
            )


if __name__ == "__main__":
    asyncio.run(main())

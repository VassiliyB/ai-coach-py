from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from pathlib import Path


class Settings(BaseSettings):
    """
    Класс конфигурации приложения.
    Автоматически считывает переменные из окружения или файла .env,
    проверяя их наличие и типы данных.
    """
    # Telegram
    TELEGRAM_BOT_TOKEN: str = Field(..., description="Токен Telegram бота от BotFather")

    # LLM (Groq)
    GROQ_API_KEY: str = Field(..., description="API-ключ Groq Cloud")
    GROQ_BASE_URL: str = Field(default="https://api.groq.com/openai/v1")
    GROQ_MODEL: str = Field(default="openai/gpt-oss-20b")

    # База данных PostgreSQL (используем асинхронный диалект asyncpg)
    DATABASE_URL: str = Field(..., description="URL подключения к БД Postgres")

    # Пути файловой системы
    BASE_DIR: Path = Path(__file__).resolve().parent
    GARMIN_TOKENS_DIR: Path = Field(default=Path(".garmin_tokens"))
    KNOWLEDGE_BASE_PATH: Path = Field(default=Path("sports_knowledge.txt"))

    # Настройки Pydantic: чтение из .env файла в кодировке UTF-8
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"  # Игнорировать лишние переменные
    )


# Создаем синглтон конфигурации для использования во всем приложении
settings = Settings()

# Автоматически создаем папку для токенов Garmin, если её нет
settings.GARMIN_TOKENS_DIR.mkdir(parents=True, exist_ok=True)
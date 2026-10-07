from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Корень проекта: папка, где лежит этот файл
BASE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    """
    Конфигурация приложения.
    Читает переменные из окружения или файла .env и валидирует их при старте.
    """

    # Telegram
    TELEGRAM_BOT_TOKEN: SecretStr = Field(..., description="Токен Telegram бота от BotFather")

    # LLM (Groq)
    GROQ_API_KEY: SecretStr = Field(..., description="API-ключ Groq Cloud")
    GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"
    GROQ_MODEL: str = "openai/gpt-oss-20b"

    # База данных PostgreSQL (асинхронный диалект asyncpg)
    DATABASE_URL: SecretStr = Field(..., description="URL подключения к БД Postgres")

    # Пути (относительные значения из .env считаются от корня проекта)
    GARMIN_TOKENS_DIR: Path = BASE_DIR / ".garmin_tokens"
    KNOWLEDGE_BASE_PATH: Path = BASE_DIR / "sports_knowledge.txt"

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("GARMIN_TOKENS_DIR", "KNOWLEDGE_BASE_PATH")
    @classmethod
    def _make_absolute(cls, value: Path) -> Path:
        """Относительные пути привязываем к корню проекта."""
        return value if value.is_absolute() else (BASE_DIR / value).resolve()


settings = Settings()

# Папка для токенов Garmin создаётся при старте, если её нет
settings.GARMIN_TOKENS_DIR.mkdir(parents=True, exist_ok=True)
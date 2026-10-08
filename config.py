from pathlib import Path
from typing import Literal, Optional

from pydantic import Field, SecretStr, field_validator, model_validator
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

    # LLM: провайдер выбирается здесь, остальной код работает через общий интерфейс generate_response
    LLM_PROVIDER: Literal["groq", "claude"] = "groq"

    # LLM (Groq)
    GROQ_API_KEY: SecretStr = Field(..., description="API-ключ Groq Cloud")
    GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"
    GROQ_MODEL: str = "openai/gpt-oss-20b"

    # LLM (Claude). Haiku 5.5 на время разработки: дешевле Opus и Sonnet
    ANTHROPIC_API_KEY: Optional[SecretStr] = Field(default=None, description="API-ключ Anthropic")
    CLAUDE_MODEL: str = "claude-haiku-5-5"
    CLAUDE_EFFORT: Literal["low", "medium", "high", "xhigh", "max"] = "medium"

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

    @model_validator(mode="after")
    def _provider_key_present(self) -> "Settings":
        """Без ключа выбранного провайдера бот упал бы только на первом запросе к модели."""
        if self.LLM_PROVIDER == "claude" and self.ANTHROPIC_API_KEY is None:
            raise ValueError("LLM_PROVIDER=claude, но ANTHROPIC_API_KEY не задан")
        return self


settings = Settings()

# Папка для токенов Garmin создаётся при старте, если её нет
settings.GARMIN_TOKENS_DIR.mkdir(parents=True, exist_ok=True)
# clients/ai_client.py
import logging
from typing import Any, Dict, List, Optional
from openai import AsyncOpenAI, APIConnectionError, RateLimitError, APIStatusError
from config import settings

logger = logging.getLogger(__name__)

# Модель по умолчанию из контекста проекта
DEFAULT_AI_MODEL = "openai/gpt-oss-20b"


class AIClientError(Exception):
    """Базовое исключение для ошибок вызова AI."""
    pass


class AIClient:
    """Асинхронный клиент для взаимодействия с Groq Cloud LLM API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.groq.com/openai/v1",
        default_model: str = DEFAULT_AI_MODEL,
    ) -> None:
        self.api_key = api_key or settings.GROQ_API_KEY
        self.default_model = default_model
        self.client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=base_url,
        )

    async def generate_response(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.4,
        max_tokens: int = 3000,
    ) -> str:
        """Отправляет контекст диалога в LLM и возвращает сгенерированный текст."""
        target_model = model or self.default_model
        try:
            logger.debug("Отправка запроса в Groq (модель: %s, сообщений: %d)", target_model, len(messages))
            response = await self.client.chat.completions.create(
                model=target_model,
                messages=messages,  # type: ignore
                temperature=temperature,
                max_tokens=max_tokens,
            )
            content = response.choices[0].message.content
            return content or ""
        except RateLimitError as exc:
            logger.error("Превышен лимит запросов к Groq (TPM/RPM): %s", exc)
            raise AIClientError("Сервер перегружен запросами. Пожалуйста, подождите минуту.") from exc
        except APIConnectionError as exc:
            logger.error("Сетевая ошибка при обращении к Groq API: %s", exc)
            raise AIClientError("Не удалось связаться с сервером ИИ. Проверьте сеть.") from exc
        except APIStatusError as exc:
            logger.error("Ошибка Groq API HTTP %s: %s", exc.status_code, exc.message)
            raise AIClientError(f"Ошибка ИИ-сервиса: {exc.message}") from exc
        except Exception as exc:
            logger.exception("Непредвиденная ошибка при запросе к AI: %s", exc)
            raise AIClientError("Произошла непредвиденная ошибка при генерации ответа.") from exc
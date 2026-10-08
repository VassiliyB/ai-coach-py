# clients/ai_client.py
import logging
from typing import Any, Dict, List, Optional

from openai import APIConnectionError, APIStatusError, AsyncOpenAI, RateLimitError
from pydantic import SecretStr

from config import settings

logger = logging.getLogger(__name__)


class AIClientError(Exception):
    """Базовое исключение для ошибок вызова AI."""
    pass


class AIClient:
    """Асинхронный клиент для взаимодействия с Groq Cloud LLM API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        default_model: Optional[str] = None,
    ) -> None:
        raw_key = api_key or settings.GROQ_API_KEY
        # В конфиге ключ хранится как SecretStr: для запроса нужна обычная строка
        self.api_key = raw_key.get_secret_value() if isinstance(raw_key, SecretStr) else raw_key
        self.default_model = default_model or settings.GROQ_MODEL
        self.client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=base_url or settings.GROQ_BASE_URL,
        )

    async def generate_response(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.4,
        max_tokens: int = 3000,
        json_mode: bool = False,
    ) -> str:
        """Отправляет контекст диалога в LLM и возвращает сгенерированный текст.

        json_mode=True включает режим JSON-объекта (в промпте должно встречаться слово JSON).
        В этом режиме обрезанный по длине ответ считается ошибкой: оборванный JSON бесполезен.
        """
        target_model = model or self.default_model
        kwargs: Dict[str, Any] = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            logger.debug(
                "Отправка запроса в Groq (модель: %s, сообщений: %d, json: %s)",
                target_model, len(messages), json_mode,
            )
            # noinspection PyTypeChecker
            response = await self.client.chat.completions.create(
                model=target_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )
            choice = response.choices[0]
            if json_mode and choice.finish_reason == "length":
                raise AIClientError("Ответ ИИ оказался обрезан по длине. Попробуйте ещё раз.")
            return choice.message.content or ""
        except AIClientError:
            raise
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
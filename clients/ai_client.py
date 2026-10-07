# clients/ai_client.py
import logging
from typing import Dict, List, Optional

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)

from config import settings

logger = logging.getLogger(__name__)

Message = Dict[str, str]


class AIClientError(Exception):
    """Ошибка вызова ИИ. Текст безопасен для показа пользователю."""


class AIClient:
    """Асинхронный клиент Groq Cloud (OpenAI-совместимый API)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        default_model: Optional[str] = None,
        timeout: float = 90.0,
        max_retries: int = 2,
    ) -> None:
        self.default_model = default_model or settings.GROQ_MODEL
        self.client = AsyncOpenAI(
            api_key=api_key or settings.GROQ_API_KEY.get_secret_value(),
            base_url=base_url or settings.GROQ_BASE_URL,
            timeout=timeout,          # без этого запрос может висеть до 10 минут
            max_retries=max_retries,  # SDK сам повторяет при 429/5xx/сетевых сбоях
        )

    async def generate_response(
        self,
        messages: List[Message],
        model: Optional[str] = None,
        temperature: float = 0.4,
        max_tokens: int = 6000,
        reasoning_effort: Optional[str] = None,
    ) -> str:
        """Отправляет диалог в LLM и возвращает текст ответа."""
        target_model = model or self.default_model

        extra_body = {"reasoning_effort": reasoning_effort} if reasoning_effort else None

        try:
            logger.debug(
                "Запрос к Groq: model=%s, messages=%d, max_tokens=%d",
                target_model, len(messages), max_tokens,
            )
            response = await self.client.chat.completions.create(
                model=target_model,
                messages=messages,  # type: ignore[arg-type]
                temperature=temperature,
                max_tokens=max_tokens,
                extra_body=extra_body,
            )
        except RateLimitError as exc:
            logger.error("Лимит запросов Groq (TPM/RPM): %s", exc)
            raise AIClientError("Сервер ИИ перегружен. Подождите минуту и повторите.") from exc
        except APITimeoutError as exc:
            logger.error("Таймаут запроса к Groq: %s", exc)
            raise AIClientError("ИИ слишком долго отвечает. Попробуйте ещё раз.") from exc
        except APIConnectionError as exc:
            logger.error("Сетевая ошибка Groq: %s", exc)
            raise AIClientError("Не удалось связаться с сервером ИИ.") from exc
        except APIStatusError as exc:
            logger.error("Groq API HTTP %s: %s", exc.status_code, exc.message)
            raise AIClientError("Сервис ИИ вернул ошибку. Попробуйте позже.") from exc
        except Exception as exc:
            logger.exception("Непредвиденная ошибка при запросе к ИИ")
            raise AIClientError("Непредвиденная ошибка при генерации ответа.") from exc

        if not response.choices:
            logger.error("Groq вернул пустой список choices")
            raise AIClientError("ИИ вернул пустой ответ. Попробуйте ещё раз.")

        choice = response.choices[0]
        content = (choice.message.content or "").strip()

        if choice.finish_reason == "length":
            logger.warning(
                "Ответ обрезан по max_tokens=%d, usage=%s", max_tokens, response.usage
            )
            if not content:
                # Модель потратила весь лимит на рассуждения
                raise AIClientError("ИИ не уложился в лимит ответа. Попробуйте ещё раз.")

        if not content:
            logger.error("Пустой content, finish_reason=%s", choice.finish_reason)
            raise AIClientError("ИИ вернул пустой ответ. Попробуйте ещё раз.")

        return content
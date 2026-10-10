# clients/ai_client.py
import asyncio
import logging
from typing import Any, Dict, List, Optional, Type

from openai import APIConnectionError, APIStatusError, AsyncOpenAI, RateLimitError
from pydantic import BaseModel, SecretStr

from clients.ai_errors import AIClientError, AIResponseFormatError
from clients.llm_usage import report_usage, usage_from_openai
from clients.rate_limit import RATE_LIMIT_RETRIES, fit_max_tokens, retry_delay
from config import settings

__all__ = ["AIClient", "AIClientError", "AIResponseFormatError"]

logger = logging.getLogger(__name__)


class AIClient:
    """Асинхронный клиент OpenAI-совместимого API: Groq Cloud (по умолчанию) или шлюз OmniRoute."""

    def __init__(
        self,
        api_key: Optional[Any] = None,
        base_url: Optional[str] = None,
        default_model: Optional[str] = None,
        tpm_limit: Optional[int] = None,
        name: str = "Groq",
    ) -> None:
        self.name = name  # для логов: какой сервис ответил ошибкой
        # Лимит токенов в минуту для подгонки max_tokens: у Groq из настроек, у шлюза свои лимиты (0)
        self.tpm_limit = settings.GROQ_TPM_LIMIT if tpm_limit is None else tpm_limit
        raw_key = api_key or settings.GROQ_API_KEY
        # В конфиге ключ хранится как SecretStr: для запроса нужна обычная строка
        self.api_key = raw_key.get_secret_value() if isinstance(raw_key, SecretStr) else raw_key
        self.default_model = default_model or settings.GROQ_MODEL
        self.client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=base_url or settings.GROQ_BASE_URL,
        )

    async def _create_with_rate_limit_retry(self, **params: Any) -> Any:
        """Запрос к модели; при 429 ждёт столько, сколько просит Groq, и повторяет.

        Бесплатный тариф ограничивает токены в минуту (8000), а один JSON-запрос занимает около 5000:
        повторные попытки генератора плана упираются в лимит, хотя ждать нужно несколько секунд.
        """
        for attempt in range(1, RATE_LIMIT_RETRIES + 2):
            try:
                # noinspection PyTypeChecker
                return await self.client.chat.completions.create(**params)
            except RateLimitError as exc:
                delay = retry_delay(exc.response.headers, exc.message) if attempt <= RATE_LIMIT_RETRIES else None
                if delay is None:
                    raise
                logger.warning(
                    "Лимит запросов %s (429), повтор %d/%d через %.1f с", self.name, attempt, RATE_LIMIT_RETRIES, delay,
                )
                await asyncio.sleep(delay)

    async def generate_response(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.4,
        max_tokens: int = 3000,
        json_mode: bool = False,
        response_schema: Optional[Type[BaseModel]] = None,
    ) -> str:
        """Отправляет контекст диалога в LLM и возвращает сгенерированный текст.

        json_mode=True включает режим JSON-объекта (в промпте должно встречаться слово JSON).
        В этом режиме обрезанный по длине ответ считается ошибкой: оборванный JSON бесполезен.
        response_schema Groq не поддерживает: схему описывает промпт, проверяет генератор.
        """
        target_model = model or self.default_model
        kwargs: Dict[str, Any] = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        # Иначе длинный запрос (повтор генератора с прошлым ответом) Groq отклонит с 413 ещё до генерации
        fitted = fit_max_tokens(messages, max_tokens, self.tpm_limit)

        try:
            logger.debug(
                "Отправка запроса в %s (модель: %s, сообщений: %d, json: %s, max_tokens: %d)",
                self.name, target_model, len(messages), json_mode, fitted,
            )
            response = await self._create_with_rate_limit_retry(
                model=target_model,
                messages=messages,
                temperature=temperature,
                max_tokens=fitted,
                **kwargs,
            )
            report_usage(usage_from_openai(response))
            choice = response.choices[0]
            if json_mode and choice.finish_reason == "length":
                raise AIResponseFormatError("Ответ ИИ оказался обрезан по длине. Попробуйте ещё раз.")
            return choice.message.content or ""
        except AIClientError:
            raise
        except RateLimitError as exc:
            logger.error("Превышен лимит запросов к %s (TPM/RPM): %s", self.name, exc)
            raise AIClientError("Сервер ИИ перегружен запросами. Попробуйте позже.") from exc
        except APIConnectionError as exc:
            logger.error("Сетевая ошибка при обращении к %s: %s", self.name, exc)
            raise AIClientError("Не удалось связаться с сервером ИИ. Проверьте сеть.") from exc
        except APIStatusError as exc:
            if json_mode and exc.status_code == 400 and getattr(exc, "code", None) == "json_validate_failed":
                logger.warning("%s отклонил ответ модели: некорректный JSON", self.name)
                raise AIResponseFormatError("ИИ вернул некорректный JSON. Попробуйте ещё раз.") from exc
            logger.error("Ошибка %s API HTTP %s: %s", self.name, exc.status_code, exc.message)
            # Текст ошибки API пользователю не показываем: он на английском и с технической выдачей
            raise AIClientError(f"Ошибка ИИ-сервиса (код {exc.status_code}). Попробуйте позже.") from exc
        except Exception as exc:
            logger.exception("Непредвиденная ошибка при запросе к AI: %s", exc)
            raise AIClientError("Произошла непредвиденная ошибка при генерации ответа.") from exc

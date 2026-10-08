# clients/claude_client.py
"""Клиент Claude (Anthropic SDK) с тем же интерфейсом generate_response, что у AIClient (Groq).

Отличия от Groq, которые прячет адаптер:
- системный промпт передаётся параметром system, а не сообщением с role=system;
- вместо JSON-режима структурированный вывод: ответ гарантированно соответствует схеме pydantic-класса;
- temperature не передаётся: новые модели Claude отклоняют параметры семплирования, глубину задаёт effort.
"""
import logging
from typing import Any, Dict, List, Optional, Tuple, Type

import anthropic
from pydantic import BaseModel

from clients.ai_errors import AIClientError, AIResponseFormatError

logger = logging.getLogger(__name__)

# Мышление входит в max_tokens: запаса генератора (6000) на рассуждения и JSON может не хватить
MIN_MAX_TOKENS = 16000


def split_system(messages: List[Dict[str, str]]) -> Tuple[str, List[Dict[str, str]]]:
    """Сообщения в формате OpenAI -> (system, сообщения без system) для Messages API."""
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    rest = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] != "system"]
    return system, rest


def extract_text(content: List[Any]) -> str:
    """Текст ответа: блоки мышления пропускаются, текстовые склеиваются."""
    return "".join(block.text for block in content if getattr(block, "type", None) == "text")


class ClaudeClient:
    """Асинхронный клиент Claude."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        default_model: Optional[str] = None,
        effort: Optional[str] = None,
    ) -> None:
        from config import settings  # ленивый импорт: функции модуля тестируются без .env

        key = api_key or settings.ANTHROPIC_API_KEY.get_secret_value()
        self.default_model = default_model or settings.CLAUDE_MODEL
        self.effort = effort or settings.CLAUDE_EFFORT
        # SDK сам повторяет 429, 5xx и сетевые ошибки с учётом retry-after
        self.client = anthropic.AsyncAnthropic(api_key=key)

    async def generate_response(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.4,
        max_tokens: int = 3000,
        json_mode: bool = False,
        response_schema: Optional[Type[BaseModel]] = None,
    ) -> str:
        """Ответ модели текстом. С response_schema текст является JSON, соответствующим схеме.

        temperature принимается ради совместимости с AIClient и не передаётся в API.
        """
        system, chat = split_system(messages)
        output_config: Dict[str, Any] = {"effort": self.effort}
        if response_schema is not None:
            output_config["format"] = {
                "type": "json_schema",
                "schema": anthropic.transform_schema(response_schema),
            }
        params: Dict[str, Any] = {
            "model": model or self.default_model,
            "max_tokens": max(max_tokens, MIN_MAX_TOKENS),
            "thinking": {"type": "adaptive"},
            "output_config": output_config,
            "messages": chat,
        }
        if system:
            params["system"] = system

        try:
            response = await self.client.messages.create(**params)
        except anthropic.RateLimitError as exc:
            logger.error("Превышен лимит запросов к Claude: %s", exc)
            raise AIClientError("Сервер ИИ перегружен запросами. Попробуйте позже.") from exc
        except anthropic.APIConnectionError as exc:
            logger.error("Сетевая ошибка при обращении к Claude API: %s", exc)
            raise AIClientError("Не удалось связаться с сервером ИИ. Проверьте сеть.") from exc
        except anthropic.APIStatusError as exc:
            logger.error("Ошибка Claude API HTTP %s: %s", exc.status_code, exc.message)
            raise AIClientError(f"Ошибка ИИ-сервиса (код {exc.status_code}). Попробуйте позже.") from exc

        usage = response.usage
        logger.info(
            "Claude %s: вход %s, выход %s токенов, stop_reason=%s",
            response.model, usage.input_tokens, usage.output_tokens, response.stop_reason,
        )
        if response.stop_reason == "refusal":
            raise AIClientError("ИИ отказался отвечать на этот запрос.")
        if response.stop_reason == "max_tokens" and (json_mode or response_schema is not None):
            raise AIResponseFormatError("Ответ ИИ оказался обрезан по длине. Попробуйте ещё раз.")
        return extract_text(response.content)

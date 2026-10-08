# clients/llm.py
"""Выбор клиента LLM по настройке LLM_PROVIDER (groq или claude)."""
from typing import Any


def create_llm_client() -> Any:
    """Клиент выбранного провайдера. У обоих одинаковый метод generate_response."""
    from config import settings  # ленивые импорты: модуль не тянет config и SDK при импорте

    if settings.LLM_PROVIDER == "claude":
        from clients.claude_client import ClaudeClient
        return ClaudeClient()
    from clients.ai_client import AIClient
    return AIClient()

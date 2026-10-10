# clients/llm.py
"""Выбор клиента LLM по настройке LLM_PROVIDER (groq, claude или omniroute)."""
from typing import Any


def create_llm_client() -> Any:
    """Клиент выбранного провайдера. У всех одинаковый метод generate_response."""
    from config import settings  # ленивые импорты: модуль не тянет config и SDK при импорте

    if settings.LLM_PROVIDER == "claude":
        from clients.claude_client import ClaudeClient
        return ClaudeClient()
    from clients.ai_client import AIClient
    if settings.LLM_PROVIDER == "omniroute":
        # Шлюз OpenAI-совместимый: тот же клиент, что у Groq, без подгонки под лимит токенов Groq
        return AIClient(
            api_key=settings.OMNIROUTE_API_KEY,
            base_url=settings.OMNIROUTE_BASE_URL,
            default_model=settings.OMNIROUTE_MODEL,
            tpm_limit=0,
            name="OmniRoute",
        )
    return AIClient()

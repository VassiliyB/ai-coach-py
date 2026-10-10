"""Выбор провайдера LLM и проверка его настроек (config.Settings, clients.llm). Без .env и сети."""
import pytest
from pydantic import ValidationError

REQUIRED = {"TELEGRAM_BOT_TOKEN": "123:test", "DATABASE_URL": "postgresql+asyncpg://u:p@localhost/db"}
OMNIROUTE_VARS = ("OMNIROUTE_API_KEY", "OMNIROUTE_MODEL", "OMNIROUTE_BASE_URL", "LLM_PROVIDER", "GROQ_TPM_LIMIT")


@pytest.fixture
def config(monkeypatch):
    """config при импорте создаёт settings: в CI без .env нужны обязательные переменные окружения."""
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    for name in OMNIROUTE_VARS:
        monkeypatch.delenv(name, raising=False)
    import config
    return config


def _settings(config, **values):
    return config.Settings(_env_file=None, **REQUIRED, **values)


def test_omniroute_needs_key_and_model(config):
    with pytest.raises(ValidationError, match="OMNIROUTE_API_KEY или OMNIROUTE_MODEL"):
        _settings(config, LLM_PROVIDER="omniroute")
    with pytest.raises(ValidationError, match="OMNIROUTE_API_KEY или OMNIROUTE_MODEL"):
        _settings(config, LLM_PROVIDER="omniroute", OMNIROUTE_API_KEY="sk-test")
    ok = _settings(config, LLM_PROVIDER="omniroute", OMNIROUTE_API_KEY="sk-test", OMNIROUTE_MODEL="combo")
    # Шлюз получает полную базу знаний: краткая нужна только под лимит Groq
    assert ok.knowledge_base_path == ok.KNOWLEDGE_BASE_PATH


def test_groq_key_required_only_for_groq(config, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY")
    with pytest.raises(ValidationError, match="GROQ_API_KEY"):
        _settings(config, LLM_PROVIDER="groq")
    _settings(config, LLM_PROVIDER="omniroute", OMNIROUTE_API_KEY="sk-test", OMNIROUTE_MODEL="combo")


def test_create_llm_client_for_omniroute(config, monkeypatch):
    settings = _settings(
        config, LLM_PROVIDER="omniroute", OMNIROUTE_API_KEY="sk-test", OMNIROUTE_MODEL="combo",
        OMNIROUTE_BASE_URL="http://gateway:20128/v1",
    )
    monkeypatch.setattr(config, "settings", settings)
    from clients.llm import create_llm_client

    client = create_llm_client()
    assert client.name == "OmniRoute"
    assert client.default_model == "combo"
    assert client.api_key == "sk-test"
    assert str(client.client.base_url) == "http://gateway:20128/v1/"
    assert client.tpm_limit == 0          # без подгонки max_tokens под лимит Groq


def test_create_llm_client_for_groq_keeps_tpm_limit(config, monkeypatch):
    monkeypatch.setattr(config, "settings", _settings(config, LLM_PROVIDER="groq"))
    import clients.ai_client as ai_client

    monkeypatch.setattr(ai_client, "settings", config.settings)   # клиент читает settings своего модуля
    from clients.llm import create_llm_client

    client = create_llm_client()
    assert (client.name, client.tpm_limit) == ("Groq", config.settings.GROQ_TPM_LIMIT)

from types import SimpleNamespace

import anthropic

from clients.claude_client import extract_text, split_system
from schemas.plan import MacroPlan, WeekPlan


def test_split_system_moves_system_out_of_messages():
    system, chat = split_system([
        {"role": "system", "content": "Ты планировщик"},
        {"role": "user", "content": "План"},
        {"role": "assistant", "content": "{}"},
        {"role": "user", "content": "Исправь"},
    ])
    assert system == "Ты планировщик"
    assert [m["role"] for m in chat] == ["user", "assistant", "user"]


def test_split_system_without_system():
    system, chat = split_system([{"role": "user", "content": "Привет"}])
    assert system == ""
    assert chat == [{"role": "user", "content": "Привет"}]


def test_extract_text_skips_thinking_blocks():
    content = [
        SimpleNamespace(type="thinking", thinking=""),
        SimpleNamespace(type="text", text='{"days": '),
        SimpleNamespace(type="text", text="[]}"),
    ]
    assert extract_text(content) == '{"days": []}'


def test_plan_schemas_convert_for_structured_outputs():
    # схемы с ограничениями pydantic должны преобразовываться без ошибок
    for model_cls in (MacroPlan, WeekPlan):
        schema = anthropic.transform_schema(model_cls)
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False

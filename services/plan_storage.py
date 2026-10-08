# services/plan_storage.py
"""Формат поля plan_details (JSONB). Чистые функции, без I/O.

Структурный план хранится как результат model_dump схемы (MacroPlan или WeekPlan).
Старые текстовые планы хранятся как {"legacy_text": "..."}: так их перенесла миграция,
и так их пишет текстовая генерация, пока /plan и планировщик не переведены на структурные планы.
"""
from typing import Any, Dict, Optional, Type, TypeVar, Union

from pydantic import BaseModel

from schemas.plan import MacroPlan, WeekPlan

LEGACY_KEY = "legacy_text"

T = TypeVar("T", bound=BaseModel)


def wrap_legacy_text(text: str) -> Dict[str, Any]:
    """Текстовый план -> значение для plan_details."""
    return {LEGACY_KEY: text or ""}


def legacy_text(details: Optional[Dict[str, Any]]) -> Optional[str]:
    """Текст старого плана или None, если план структурный (или пустой)."""
    if not isinstance(details, dict):
        return None
    text = details.get(LEGACY_KEY)
    return text if isinstance(text, str) else None


def plan_to_details(plan: Union[MacroPlan, WeekPlan, str]) -> Dict[str, Any]:
    """План -> значение для plan_details. Строка сохраняется в старом формате (legacy_text)."""
    if isinstance(plan, str):
        return wrap_legacy_text(plan)
    return plan.model_dump(mode="json")


def _parse(details: Optional[Dict[str, Any]], model_cls: Type[T]) -> Optional[T]:
    if not isinstance(details, dict) or LEGACY_KEY in details:
        return None
    # Повреждённый структурный план это ошибка, а не старый формат: ValidationError идёт наверх
    return model_cls.model_validate(details)


def parse_macro(details: Optional[Dict[str, Any]]) -> Optional[MacroPlan]:
    """plan_details макроцикла -> MacroPlan. None для старого текстового плана."""
    return _parse(details, MacroPlan)


def parse_week(details: Optional[Dict[str, Any]]) -> Optional[WeekPlan]:
    """plan_details недели -> WeekPlan. None для старого текстового плана."""
    return _parse(details, WeekPlan)

# services/plan_storage.py
"""Формат поля plan_details (JSONB). Чистые функции, без I/O.

Структурный план хранится как результат model_dump схемы (MacroPlan или WeekPlan).
Старые текстовые планы миграция перенесла как {"legacy_text": "..."}: структурного разбора у них нет.
"""
from typing import Any, Dict, Optional, Type, TypeVar, Union

from pydantic import BaseModel

from schemas.plan import MacroPlan, WeekPlan

LEGACY_KEY = "legacy_text"

T = TypeVar("T", bound=BaseModel)


def plan_to_details(plan: Union[MacroPlan, WeekPlan]) -> Dict[str, Any]:
    """План -> значение для plan_details."""
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

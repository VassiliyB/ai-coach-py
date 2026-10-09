from dataclasses import dataclass
from datetime import datetime
from typing import Dict

from sqlalchemy import distinct, func, insert, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import AppUser, LlmUsage


@dataclass(frozen=True)
class UsageTotals:
    input_tokens: int          # вход без кэша
    cache_tokens: int          # чтение и запись кэша
    output_tokens: int
    requests: int              # запросы пользователя (вопрос /ask, разбор, неделя), не вызовы модели


class UsageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(
        self, chat_id: int, command: str, request_id: str, model: str,
        input_tokens: int, output_tokens: int, cache_read_tokens: int, cache_write_tokens: int,
    ) -> None:
        """Запись по chat_id одним запросом (INSERT ... SELECT): пользователя нет — строки нет."""
        source = select(
            AppUser.id, literal(command), literal(request_id), literal(model),
            literal(input_tokens), literal(output_tokens), literal(cache_read_tokens), literal(cache_write_tokens),
        ).where(AppUser.telegram_chat_id == chat_id)
        await self.session.execute(
            insert(LlmUsage).from_select(
                ["user_id", "command", "request_id", "model", "input_tokens", "output_tokens",
                 "cache_read_tokens", "cache_write_tokens"],
                source,
            )
        )

    async def count_requests(self, chat_id: int, command: str, since: datetime) -> int:
        """Сколько запросов команды пользователь сделал с момента since (например, вопросов /ask за день)."""
        stmt = (
            select(func.count(distinct(LlmUsage.request_id)))
            .join(AppUser, AppUser.id == LlmUsage.user_id)
            .where(AppUser.telegram_chat_id == chat_id, LlmUsage.command == command, LlmUsage.created_at >= since)
        )
        return (await self.session.execute(stmt)).scalar_one()

    async def totals_by_chat(self, since: datetime) -> Dict[int, UsageTotals]:
        """Расход каждого пользователя с момента since: {chat_id: итоги}."""
        stmt = (
            select(
                AppUser.telegram_chat_id,
                func.sum(LlmUsage.input_tokens),
                func.sum(LlmUsage.cache_read_tokens + LlmUsage.cache_write_tokens),
                func.sum(LlmUsage.output_tokens),
                func.count(distinct(LlmUsage.request_id)),
            )
            .join(AppUser, AppUser.id == LlmUsage.user_id)
            .where(LlmUsage.created_at >= since)
            .group_by(AppUser.telegram_chat_id)
        )
        rows = (await self.session.execute(stmt)).all()
        return {chat_id: UsageTotals(int(i), int(c), int(o), int(r)) for chat_id, i, c, o, r in rows}

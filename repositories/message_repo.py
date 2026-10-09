from datetime import datetime
from typing import List, Tuple

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import ChatMessage


class MessageRepository:
    """История вопросов /ask и ответов тренера (chat_messages)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add_exchange(self, user_id: int, question: str, answer: str) -> None:
        self.session.add_all([
            ChatMessage(user_id=user_id, role="user", content=question),
            ChatMessage(user_id=user_id, role="assistant", content=answer),
        ])
        await self.session.flush()

    async def recent(self, user_id: int, since: datetime, limit: int) -> List[Tuple[str, str]]:
        """Последние limit сообщений после since, от старых к новым: [(role, content)]."""
        stmt = (
            select(ChatMessage.role, ChatMessage.content)
            .where(ChatMessage.user_id == user_id, ChatMessage.created_at >= since)
            .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).all()
        return [(role, content) for role, content in reversed(rows)]

    async def delete_older(self, user_id: int, before: datetime) -> None:
        await self.session.execute(
            delete(ChatMessage).where(ChatMessage.user_id == user_id, ChatMessage.created_at < before)
        )

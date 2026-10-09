from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Computed, DateTime, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, utcnow

# Поисковый вектор считает PostgreSQL: заголовок раздела весит больше текста (A против B), ё приводится к е,
# чтобы «лёгкий» и «легкий» находили друг друга. Выражение повторяется в миграции 1b7e5c9d3a2f
SEARCH_VECTOR_SQL = (
    "setweight(to_tsvector('russian'::regconfig, translate(coalesce(chapter, '') || ' ' || coalesce(section, ''),"
    " 'ёЁ', 'еЕ')), 'A') || "
    "setweight(to_tsvector('russian'::regconfig, translate(content, 'ёЁ', 'еЕ')), 'B')"
)


class BookChunk(Base):
    """Фрагмент книги для поиска в /ask. Данные не пользовательские: FK на app_users нет,
    заполняет скрипт ingest_books.py (книга целиком заменяется при повторной загрузке)."""

    __tablename__ = "book_chunks"
    __table_args__ = (
        UniqueConstraint("book_key", "position", name="uq_book_chunks_book_position"),
        Index("ix_book_chunks_search_vector", "search_vector", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # Имя файла книги без расширения: по нему повторная загрузка находит старые фрагменты
    book_key: Mapped[str] = mapped_column(String(255), nullable=False)
    author: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    chapter: Mapped[str] = mapped_column(String(500), nullable=False)
    section: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # Порядковый номер фрагмента в книге: соседние фрагменты можно подтянуть для контекста
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    search_vector: Mapped[str] = mapped_column(TSVECTOR, Computed(SEARCH_VECTOR_SQL, persisted=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )

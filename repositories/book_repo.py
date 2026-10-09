from typing import List, Sequence, Tuple

from sqlalchemy import case, delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import BookChunk

# Тот же словарь, что в BookChunk.search_vector: запрос и текст должны нормализоваться одинаково
_TS_CONFIG = "russian"


class BookRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def replace_book(self, book_key: str, rows: Sequence[dict]) -> None:
        """Удаляет прежние фрагменты книги и вставляет новые (повторная загрузка той же книги)."""
        await self.session.execute(delete(BookChunk).where(BookChunk.book_key == book_key))
        if rows:
            await self.session.execute(insert(BookChunk), [dict(row, book_key=book_key) for row in rows])
        await self.session.flush()

    async def list_books(self) -> List[Tuple[str, str, str, int]]:
        """(book_key, автор, название, число фрагментов) по всем загруженным книгам."""
        stmt = (
            select(BookChunk.book_key, BookChunk.author, BookChunk.title, func.count())
            .group_by(BookChunk.book_key, BookChunk.author, BookChunk.title)
            .order_by(BookChunk.author, BookChunk.title)
        )
        return [tuple(row) for row in (await self.session.execute(stmt)).all()]

    async def search(self, terms: Sequence[str], limit: int = 6) -> List[BookChunk]:
        """Фрагменты, где встречается хотя бы одно из слов или фраз, лучшие первыми.
        Сначала по числу совпавших слов запроса: иначе частое слово («VDOT», «темп») с десятком повторов
        перевешивает фрагмент, где совпали три разных слова вопроса. Внутри равного числа порядок
        задаёт ts_rank_cd (частота и близость слов, заголовок раздела весит больше текста).
        websearch_to_tsquery не падает на любом вводе; фраза из нескольких слов ищется целиком."""
        cleaned = [t.replace("ё", "е").replace("Ё", "Е").replace('"', " ").strip() for t in terms]
        cleaned = list(dict.fromkeys(f'"{t}"' if " " in t else t for t in cleaned if t))
        if not cleaned:
            return []
        vector = BookChunk.search_vector
        query = func.websearch_to_tsquery(_TS_CONFIG, " or ".join(cleaned))
        coverage = sum(
            case((vector.op("@@")(func.websearch_to_tsquery(_TS_CONFIG, term)), 1), else_=0) for term in cleaned
        )
        rank = func.ts_rank_cd(vector, query)
        stmt = (
            select(BookChunk)
            .where(vector.op("@@")(query))
            .order_by(coverage.desc(), rank.desc(), BookChunk.id)
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

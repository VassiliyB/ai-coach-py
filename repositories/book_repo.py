import math
from typing import List, Sequence, Tuple

from sqlalchemy import case, delete, func, insert, literal, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import BookChunk

# Тот же словарь, что в BookChunk.search_vector: запрос и текст должны нормализоваться одинаково
_TS_CONFIG = "russian"
# Главное понятие вопроса (первое в списке) весит больше остальных
MAIN_CONCEPT_FACTOR = 2.0


def concept_weight(total: int, frequency: int) -> float:
    """Вес понятия по редкости (IDF как в BM25): встречается в немногих фрагментах -> весит больше.
    Понятие почти в каждом фрагменте даёт вес около нуля, но не отрицательный."""
    return math.log(1 + (total - frequency + 0.5) / (frequency + 0.5))


def _or_query(variants: Sequence[str]) -> str:
    """Варианты понятия -> строка для websearch_to_tsquery: «"легкий бег" or Л-темп»."""
    cleaned = [v.replace("ё", "е").replace("Ё", "Е").replace('"', " ").strip() for v in variants]
    return " or ".join(dict.fromkeys(f'"{v}"' if " " in v else v for v in cleaned if v))


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

    async def search(self, groups: Sequence[Sequence[str]], limit: int = 6) -> List[BookChunk]:
        """Фрагменты, где встречается хотя бы одно понятие запроса, лучшие первыми.

        groups: понятия по важности (главное первым), каждое списком равнозначных вариантов
        (синонимы из coach_qa.expand_terms). Счёт фрагмента: сумма весов совпавших понятий
        (concept_weight: редкое весит больше частого), главное понятие ×MAIN_CONCEPT_FACTOR,
        плюс столько же, если оно в названии раздела (раздел «Перерывы» про перерывы,
        а не упоминает их мимоходом). При равном счёте порядок задаёт ts_rank_cd.
        Без этого длинные фрагменты с планами, где понемногу есть всё, обходили точные разделы.
        websearch_to_tsquery не падает на любом вводе; фраза из нескольких слов ищется целиком."""
        queries = list(dict.fromkeys(q for q in (_or_query(group) for group in groups) if q))
        if not queries:
            return []
        vector = BookChunk.search_vector
        # Название раздела (у вступления главы раздела нет, тогда главы): не индексируется, но фрагментов
        # сотни, а название главы целиком дало бы бонус всем её фрагментам
        heading_vector = func.to_tsvector(
            literal_column("'russian'::regconfig"),
            func.translate(func.coalesce(BookChunk.section, BookChunk.chapter), "ёЁ", "еЕ"),
        )
        tsqueries = [func.websearch_to_tsquery(_TS_CONFIG, q) for q in queries]
        matches = [vector.op("@@")(q) for q in tsqueries]

        # Сколько фрагментов содержит каждое понятие: одним проходом по таблице
        counts = (await self.session.execute(
            select(func.count(), *(func.count().filter(m) for m in matches))
        )).one()
        total, frequencies = counts[0], counts[1:]

        terms = []
        for index, (tsquery, match, df) in enumerate(zip(tsqueries, matches, frequencies, strict=True)):
            if not df:
                continue
            weight = concept_weight(total, df)
            factor = MAIN_CONCEPT_FACTOR if index == 0 else 1.0
            terms.append(case((match, literal(weight * factor)), else_=literal(0.0)))
            terms.append(case((heading_vector.op("@@")(tsquery), literal(weight * factor)), else_=literal(0.0)))
        if not terms:
            return []

        query = func.websearch_to_tsquery(_TS_CONFIG, " or ".join(queries))
        stmt = (
            select(BookChunk)
            .where(vector.op("@@")(query))
            .order_by(sum(terms).desc(), func.ts_rank_cd(vector, query).desc(), BookChunk.id)
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

"""Загрузка книг EPUB в таблицу book_chunks для поиска в /ask.

    python ingest_books.py F:\\books\\running             # все *.epub из папки (подпапки не читаются)
    python ingest_books.py F:\\books\\running --dry-run   # только разбор и статистика, БД не трогается
    python ingest_books.py --list                         # что уже загружено

Повторная загрузка книги заменяет её фрагменты целиком (ключ: имя файла без расширения).
Файлы книг в репозиторий не кладутся: текст хранится только в вашей БД.
В Docker: docker compose run --rm -v F:/books/running:/books:ro bot python ingest_books.py /books
"""
import argparse
import asyncio
import sys
from dataclasses import asdict
from pathlib import Path
from typing import List

from services.book_ingest import Book, read_epub


def _print_book(book: Book, samples: int) -> None:
    words = [len(c.content.split()) for c in book.chunks]
    chapters = list(dict.fromkeys(c.chapter for c in book.chunks))
    print(f"\n{book.author} — «{book.title}»  [{book.key}]")
    if not words:
        print("  фрагментов нет: проверьте, что это книга с текстом, а не со сканами")
        return
    print(f"  фрагментов: {len(words)}, слов: {sum(words)}, в фрагменте: {min(words)}–{max(words)}, "
          f"в среднем {sum(words) // len(words)}")
    print(f"  глав: {len(chapters)}")
    for chapter in chapters:
        print(f"    {chapter}")
    step = max(1, len(book.chunks) // max(1, samples))
    for chunk in book.chunks[::step][:samples]:
        print(f"\n  --- №{chunk.position} | {chunk.chapter} | {chunk.section or '—'}")
        print("  " + chunk.content[:400].replace("\n", "\n  ") + ("…" if len(chunk.content) > 400 else ""))


async def _save(books: List[Book]) -> None:
    from database import async_session_maker  # ленивый импорт: --dry-run работает без .env и БД
    from repositories.book_repo import BookRepository

    async with async_session_maker() as session:
        repo = BookRepository(session)
        for book in books:
            rows = [
                {"author": book.author, "title": book.title, **asdict(chunk)}
                for chunk in book.chunks
            ]
            await repo.replace_book(book.key, rows)
            print(f"Загружено: {book.author} — «{book.title}», фрагментов {len(rows)}")
        await session.commit()


async def _list() -> None:
    from database import async_session_maker
    from repositories.book_repo import BookRepository

    async with async_session_maker() as session:
        books = await BookRepository(session).list_books()
    if not books:
        print("Книг в БД нет")
    for key, author, title, count in books:
        print(f"{author} — «{title}»: фрагментов {count}  [{key}]")


def main() -> int:
    parser = argparse.ArgumentParser(description="Загрузка книг EPUB в book_chunks")
    parser.add_argument("folder", nargs="?", type=Path, help="папка с файлами .epub")
    parser.add_argument("--dry-run", action="store_true", help="только разобрать и показать статистику")
    parser.add_argument("--samples", type=int, default=3, help="сколько примеров фрагментов показать в --dry-run")
    parser.add_argument("--list", action="store_true", help="показать загруженные книги")
    args = parser.parse_args()

    if args.list:
        asyncio.run(_list())
        return 0
    if args.folder is None or not args.folder.is_dir():
        parser.error("укажите существующую папку с книгами")

    files = sorted(args.folder.glob("*.epub"))
    if not files:
        print(f"В {args.folder} нет файлов .epub")
        return 1
    books = [read_epub(path) for path in files]
    if args.dry_run:
        for book in books:
            _print_book(book, args.samples)
        return 0
    asyncio.run(_save(books))
    return 0


if __name__ == "__main__":
    sys.exit(main())

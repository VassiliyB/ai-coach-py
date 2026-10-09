"""Разбор книг EPUB на фрагменты для полнотекстового поиска (/ask).

EPUB это zip с XHTML-файлами: порядок чтения задаёт spine в .opf, автор и название лежат там же.
Хватает стандартной библиотеки, отдельной зависимости для бота нет. Таблицы-картинки в поиск не попадают,
HTML-таблицы идут строками «ячейка | ячейка». Запись в БД делает скрипт ingest_books.py.
"""
import posixpath
import re
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import List, Optional, Tuple

# Размер фрагмента в словах: для поиска лучше короткие фрагменты (точнее ранжирование),
# для ответа модели нужна законченная мысль. Абзац длиннее MAX режется по предложениям
CHUNK_TARGET_WORDS = 300
CHUNK_MAX_WORDS = 450
# Хвост раздела короче этого присоединяется к предыдущему фрагменту, а не живёт отдельно
CHUNK_MIN_WORDS = 80
# Файлы книги с меньшим объёмом текста (титул, посвящение, разделитель «Часть I») пропускаются
MIN_DOC_WORDS = 150

# Служебные разделы издательства: в поиске они только мешают
SKIP_TITLES = {
    "информация от издательства", "оглавление", "содержание", "примечания", "об авторе", "об авторах",
    "благодарности", "посвящение", "над книгой работали", "эту книгу хорошо дополняют:",
    "максимально полезные книги", "все права защищены",
}

# Подписи к таблицам и рисункам: сами таблицы в EPUB картинками, подпись без них ничего не даёт
_CAPTION_RE = re.compile(r"^(таблица|табл\.|рис\.|рисунок)\s*[\dа-яa-z]", re.IGNORECASE)
_SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+(?=[«\"(A-ZА-ЯЁ0-9—–-])")
_SPACES_RE = re.compile(r"\s+")

_BLOCK_TAGS = {"p", "li", "blockquote", "dd", "dt", "figcaption"}
_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_SKIP_TAGS = {"head", "script", "style", "title"}


@dataclass(frozen=True)
class Block:
    """Заголовок (level 1..6) или абзац текста (level 0)."""
    level: int
    text: str

    @property
    def is_heading(self) -> bool:
        return self.level > 0


@dataclass(frozen=True)
class Chunk:
    chapter: str
    section: Optional[str]
    position: int
    content: str


@dataclass(frozen=True)
class Book:
    key: str
    author: str
    title: str
    chunks: List[Chunk]


def clean_text(text: str) -> str:
    """Неразрывные пробелы и мягкие переносы из вёрстки, лишние пробелы."""
    text = text.replace("\xa0", " ").replace("\xad", "").replace("​", "")
    return _SPACES_RE.sub(" ", text).strip()


def _word_count(text: str) -> int:
    return len(text.split())


class _BlockParser(HTMLParser):
    """XHTML главы -> список Block. Таблица превращается в строки «a | b | c»."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: List[Block] = []
        self._buf: List[str] = []
        self._heading_level = 0
        self._skip_depth = 0
        self._footnote_depth = 0
        self._table_depth = 0
        self._row: Optional[List[str]] = None

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag == "a":
            attr = dict(attrs)
            marker = f"{attr.get('class') or ''} {attr.get('href') or ''}".lower()
            if self._footnote_depth or "footnote" in marker:
                self._footnote_depth += 1
            return
        if tag == "table":
            self._flush()
            self._table_depth += 1
        elif tag == "tr" and self._table_depth:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._buf = []
        elif tag in _HEADING_TAGS and not self._table_depth:
            self._flush()
            self._heading_level = int(tag[1])
        elif tag in _BLOCK_TAGS:
            if self._table_depth:
                # несколько абзацев в ячейке это шаги тренировки: «5 минут в зоне 1; 10 минут в зоне 2»
                if "".join(self._buf).strip():
                    self._buf.append("; ")
            else:
                self._flush()
        elif tag == "br":
            self._buf.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag == "a":
            if self._footnote_depth:
                self._footnote_depth -= 1
            return
        if tag in ("td", "th") and self._row is not None:
            self._row.append(clean_text("".join(self._buf)))
            self._buf = []
        elif tag == "tr" and self._row is not None:
            cells = [c for c in self._row if c]
            if cells:
                self.blocks.append(Block(0, " | ".join(cells)))
            self._row = None
        elif tag == "table" and self._table_depth:
            self._table_depth -= 1
            self._buf = []
        elif tag in _HEADING_TAGS and not self._table_depth:
            self._flush()
        elif tag in _BLOCK_TAGS and not self._table_depth:
            self._flush()

    def handle_data(self, data: str) -> None:
        if not self._skip_depth and not self._footnote_depth:
            self._buf.append(data)

    def close(self) -> None:
        super().close()
        self._flush()

    def _flush(self) -> None:
        if self._table_depth:
            return
        text = clean_text("".join(self._buf))
        self._buf = []
        level, self._heading_level = self._heading_level, 0
        if text and not _CAPTION_RE.match(text):
            self.blocks.append(Block(level, text))


def html_to_blocks(html: str) -> List[Block]:
    parser = _BlockParser()
    parser.feed(html)
    parser.close()
    return parser.blocks


def chapter_title(blocks: List[Block]) -> Tuple[Optional[str], int]:
    """Название главы по первым заголовкам файла и число заголовков, которые оно заняло.
    Вёрстка МИФ: номер главы отдельным заголовком («5»), название следующим: «Глава 5. Система…»."""
    heads = []
    for block in blocks:
        if not block.is_heading:
            break
        heads.append(block.text)
        if len(heads) == 2:
            break
    if not heads:
        return None, 0
    if heads[0].isdigit() and len(heads) == 2:
        return f"Глава {heads[0]}. {heads[1]}", 2
    return heads[0], 1


def _split_long(text: str) -> List[str]:
    """Абзац длиннее CHUNK_MAX_WORDS режется по границам предложений."""
    if _word_count(text) <= CHUNK_MAX_WORDS:
        return [text]
    parts, current = [], []
    for sentence in _SENTENCE_RE.split(text):
        if current and _word_count(" ".join(current + [sentence])) > CHUNK_TARGET_WORDS:
            parts.append(" ".join(current))
            current = []
        current.append(sentence)
    if current:
        parts.append(" ".join(current))
    return parts


def chunk_document(blocks: List[Block], start_position: int = 0) -> List[Chunk]:
    """Абзацы одного файла главы -> фрагменты. Фрагмент не пересекает границу раздела,
    кроме короткого хвоста (< CHUNK_MIN_WORDS), который прилипает к предыдущему фрагменту того же раздела."""
    chapter, used = chapter_title(blocks)
    if chapter is None:
        return []
    # (раздел, абзацы) по порядку: каждый заголовок после названия главы открывает новый раздел
    sections: List[Tuple[Optional[str], List[str]]] = [(None, [])]
    for block in blocks[used:]:
        if block.is_heading:
            sections.append((block.text, []))
        else:
            sections[-1][1].extend(_split_long(block.text))

    chunks: List[Chunk] = []
    position = start_position
    for section, paragraphs in sections:
        section_words = _word_count(" ".join(paragraphs))
        if not section_words:
            continue
        # Короткий раздел (описание упражнения, врезка) один ничего не даёт поиску: дописываем его
        # с заголовком к предыдущему фрагменту главы, если тот не станет слишком длинным
        if (
            chunks and section_words < CHUNK_MIN_WORDS
            and _word_count(chunks[-1].content) + section_words <= CHUNK_MAX_WORDS
        ):
            merged = "\n".join([chunks[-1].content, section or ""] + paragraphs).replace("\n\n", "\n")
            chunks[-1] = Chunk(chapter, chunks[-1].section, chunks[-1].position, merged)
            continue
        groups: List[List[str]] = []
        for paragraph in paragraphs:
            if groups and _word_count(" ".join(groups[-1] + [paragraph])) <= CHUNK_TARGET_WORDS:
                groups[-1].append(paragraph)
            else:
                groups.append([paragraph])
        if len(groups) > 1 and _word_count(" ".join(groups[-1])) < CHUNK_MIN_WORDS:
            tail = groups.pop()
            groups[-1].extend(tail)
        for group in groups:
            chunks.append(Chunk(chapter, section, position, "\n".join(group)))
            position += 1
    return chunks


def _is_skipped(blocks: List[Block]) -> bool:
    title, _ = chapter_title(blocks)
    if title is None or title.lower().rstrip(".") in SKIP_TITLES:
        return True
    return sum(_word_count(b.text) for b in blocks if not b.is_heading) < MIN_DOC_WORDS


def _opf_meta(opf: str, tag: str) -> str:
    match = re.search(rf"<dc:{tag}[^>]*>(.*?)</dc:{tag}>", opf, re.DOTALL)
    return clean_text(re.sub(r"<[^>]+>", "", match.group(1))) if match else ""


def clean_title(title: str) -> str:
    """«От 800 метров до марафона_5 изд.» -> «От 800 метров до марафона»: хвост издания после «_» не нужен."""
    return clean_text(title.split("_", 1)[0]) or title


def _spine_paths(archive: zipfile.ZipFile) -> Tuple[str, List[str]]:
    """Путь .opf и файлы глав в порядке чтения."""
    container = archive.read("META-INF/container.xml").decode("utf-8")
    opf_path = re.search(r'full-path="([^"]+)"', container).group(1)
    opf = archive.read(opf_path).decode("utf-8")
    manifest = {}
    for item in re.findall(r"<item\b[^>]*>", opf):
        item_id = re.search(r'\bid="([^"]+)"', item)
        href = re.search(r'\bhref="([^"]+)"', item)
        if item_id and href:
            manifest[item_id.group(1)] = href.group(1)
    base = posixpath.dirname(opf_path)
    paths = [
        posixpath.normpath(posixpath.join(base, manifest[idref]))
        for idref in re.findall(r'<itemref\b[^>]*\bidref="([^"]+)"', opf)
        if idref in manifest
    ]
    return opf, paths


def read_epub(path: Path) -> Book:
    """EPUB -> Book с фрагментами всех содержательных глав."""
    with zipfile.ZipFile(path) as archive:
        opf, paths = _spine_paths(archive)
        chunks: List[Chunk] = []
        for doc_path in paths:
            blocks = html_to_blocks(archive.read(doc_path).decode("utf-8", errors="replace"))
            if not _is_skipped(blocks):
                chunks.extend(chunk_document(blocks, start_position=len(chunks)))
    return Book(
        key=path.stem,
        author=_opf_meta(opf, "creator") or "Неизвестный автор",
        title=clean_title(_opf_meta(opf, "title")) or path.stem,
        chunks=chunks,
    )

import zipfile

from services.book_ingest import (
    CHUNK_MAX_WORDS,
    CHUNK_MIN_WORDS,
    CHUNK_TARGET_WORDS,
    Block,
    chapter_title,
    chunk_document,
    clean_text,
    clean_title,
    html_to_blocks,
    read_epub,
)


def words(n: int, word: str = "слово") -> str:
    return " ".join([word] * n) + "."


def test_clean_text_removes_layout_artifacts():
    assert clean_text("лег\xadкий\xa0бег \n  в  зоне") == "легкий бег в зоне"


def test_clean_title_drops_edition_suffix():
    assert clean_title("От 800 метров до марафона_5 изд.") == "От 800 метров до марафона"
    assert clean_title("Бег по правилу 80/20") == "Бег по правилу 80/20"


def test_html_to_blocks_headings_paragraphs_footnotes_captions():
    html = """<html><head><title>Глава</title></head><body>
      <h2>5</h2><h2>Система&#160;VDOT</h2>
      <p>Текст<a class="_idFootnoteLink" href="links.xhtml#footnote-001">6</a> абзаца.</p>
      <h4 class="_table_name">Таблица 5.2. Интенсивность тренировки</h4>
      <div class="img"><img src="t91.jpg"/></div>
      <p>Рис. 5.1. Кривая потребления кислорода</p>
      <h3>Раздел</h3><p>Второй абзац</p>
    </body></html>"""
    assert html_to_blocks(html) == [
        Block(2, "5"),
        Block(2, "Система VDOT"),
        Block(0, "Текст абзаца."),
        Block(3, "Раздел"),
        Block(0, "Второй абзац"),
    ]


def test_html_table_becomes_rows_with_steps():
    html = """<p>До таблицы</p><table><thead><tr><td><p><b>Название</b></p></td><td><p>Формат</p></td></tr></thead>
      <tbody><tr><td><p>Базовая пробежка 1 (20:00)</p></td>
      <td><p>5 минут в зоне 1</p><p>10 минут в зоне 2</p></td></tr></tbody></table><p>После</p>"""
    assert [b.text for b in html_to_blocks(html)] == [
        "До таблицы",
        "Название | Формат",
        "Базовая пробежка 1 (20:00) | 5 минут в зоне 1; 10 минут в зоне 2",
        "После",
    ]


def test_chapter_title_number_and_name():
    assert chapter_title([Block(2, "5"), Block(2, "Система VDOT"), Block(0, "текст")]) == (
        "Глава 5. Система VDOT", 2)
    assert chapter_title([Block(1, "Предисловие"), Block(0, "текст")]) == ("Предисловие", 1)
    assert chapter_title([Block(0, "текст без заголовка")]) == (None, 0)


def test_chunks_respect_target_and_sections():
    paragraph = words(100)
    blocks = [Block(1, "Введение")] + [Block(0, paragraph)] * 7 + [Block(2, "Раздел")] + [Block(0, paragraph)] * 2
    chunks = chunk_document(blocks, start_position=10)
    assert [c.position for c in chunks] == list(range(10, 10 + len(chunks)))
    assert all(len(c.content.split()) <= CHUNK_TARGET_WORDS + CHUNK_MIN_WORDS for c in chunks)
    # хвост в 100 слов (>= CHUNK_MIN_WORDS) остаётся отдельным фрагментом
    assert [c.section for c in chunks] == [None, None, None, "Раздел"]
    assert all(c.chapter == "Введение" for c in chunks)


def test_short_tail_merges_into_previous_chunk():
    blocks = [Block(1, "Глава"), Block(0, words(CHUNK_TARGET_WORDS - 10)), Block(0, words(CHUNK_MIN_WORDS - 10))]
    chunks = chunk_document(blocks)
    assert len(chunks) == 1


def test_short_section_merges_with_its_heading():
    blocks = [Block(1, "Упражнения"), Block(0, words(150)), Block(3, "Кариока"), Block(0, "Выполните 20 шагов.")]
    chunks = chunk_document(blocks)
    assert len(chunks) == 1
    assert chunks[0].content.endswith("Кариока\nВыполните 20 шагов.")
    assert chunks[0].section is None


def test_long_paragraph_split_by_sentences():
    sentence = "Бегите легко и спокойно в разговорном темпе без напряжения сегодня."
    paragraph = " ".join([sentence] * 60)  # 600 слов одним абзацем
    chunks = chunk_document([Block(1, "Глава"), Block(0, paragraph)])
    assert len(chunks) >= 2
    assert all(len(c.content.split()) <= CHUNK_MAX_WORDS for c in chunks)
    assert all(c.content.endswith(".") for c in chunks)


def _make_epub(path, docs):
    manifest = "".join(f'<item id="d{i}" href="Text/{name}" media-type="application/xhtml+xml"/>'
                       for i, (name, _) in enumerate(docs))
    spine = "".join(f'<itemref idref="d{i}"/>' for i in range(len(docs)))
    opf = (f'<package><metadata><dc:title>Формула бега_3 изд.</dc:title><dc:creator>Джек Дэниелс</dc:creator>'
           f'</metadata><manifest>{manifest}</manifest><spine>{spine}</spine></package>')
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml",
                         '<container><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>')
        archive.writestr("OEBPS/content.opf", opf)
        for name, html in docs:
            archive.writestr(f"OEBPS/Text/{name}", html)


def test_read_epub_follows_spine_and_skips_service_pages(tmp_path):
    body = f"<p>{words(200, 'темп')}</p>"
    docs = [
        ("g2.xhtml", f"<h2>2</h2><h2>Принципы</h2>{body}"),
        ("inf.xhtml", f"<h1>Информация от издательства</h1>{body}"),
        ("C1.xhtml", "<h1>Часть I</h1><h1>Что такое тренировка</h1>"),
        ("g1.xhtml", f"<h2>1</h2><h2>Слагаемые успеха</h2>{body}"),
    ]
    path = tmp_path / "Дэниелс.epub"
    _make_epub(path, docs)
    book = read_epub(path)
    assert (book.key, book.author, book.title) == ("Дэниелс", "Джек Дэниелс", "Формула бега")
    # порядок из spine, служебная страница и разделитель части пропущены
    assert [c.chapter for c in book.chunks] == ["Глава 2. Принципы", "Глава 1. Слагаемые успеха"]
    assert [c.position for c in book.chunks] == [0, 1]

from services.coach_qa import (
    HISTORY_ANSWER_CHARS,
    Exchange,
    format_history,
    history_questions,
    pair_exchanges,
)


def test_pair_exchanges_keeps_last_complete_pairs():
    messages = [
        ("user", "в1"), ("assistant", "о1"),
        ("user", "в2"), ("assistant", "о2"),
        ("user", "в3"), ("assistant", "о3"),
    ]
    assert pair_exchanges(messages) == [Exchange("в2", "о2"), Exchange("в3", "о3")]
    assert pair_exchanges(messages, limit=1) == [Exchange("в3", "о3")]
    assert pair_exchanges(messages, limit=0) == []


def test_pair_exchanges_skips_broken_pairs():
    # Окно отрезало вопрос первого ответа; вопрос без ответа в конце тоже не пара
    messages = [("assistant", "о0"), ("user", "в1"), ("assistant", "о1"), ("user", "в2")]
    assert pair_exchanges(messages) == [Exchange("в1", "о1")]
    assert pair_exchanges([]) == []


def test_format_history_strips_html_and_shortens():
    long_answer = "<b>Лёгкий</b> бег &amp; " + "слово " * 400
    text = format_history([Exchange("Какой  темп\nлёгкий?", long_answer), Exchange("а <script>?", "ок")])
    assert text.startswith("Вопрос 1: Какой темп лёгкий?\nОтвет 1: Лёгкий бег & слово")
    first_answer = text.split("\n")[1]
    assert len(first_answer) <= len("Ответ 1: ") + HISTORY_ANSWER_CHARS + 1 and first_answer.endswith("…")
    assert "<" not in text and ">" not in text                   # история идёт внутрь <data>
    assert "Вопрос 2: а  script ?\nОтвет 2: ок" in text
    assert format_history([]) == ""


def test_history_questions_only_questions():
    assert history_questions([Exchange("Сколько отдыхать\nмежду 1000 м?", "2–3 мин"), Exchange("а <б>?", "x")]) == (
        "Сколько отдыхать между 1000 м?\nа  б ?"
    )
    assert history_questions([]) == ""

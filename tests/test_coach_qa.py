from datetime import date

from schemas.plan import MacroPlan, WeekPlan
from services.coach_qa import (
    MAX_SEARCH_TERMS,
    SYNONYM_GROUPS,
    Fragment,
    cited_numbers,
    expand_terms,
    fallback_terms,
    format_fragments,
    plan_context,
    render_sources,
    short_author,
    tidy_heading,
)
from tests.test_plan_schemas import valid_macro, valid_week_days


def group_with(word: str):
    return next(g for g in SYNONYM_GROUPS if word in g)


def test_expand_terms_maps_both_translations_to_one_group():
    groups = expand_terms(["длительная пробежка", "Д-бег", "марафон"])
    # «длительная пробежка» и «Д-бег» это одно понятие: одна группа, без повторов
    assert groups == [group_with("длинная пробежка"), ["марафон"]]


def test_expand_terms_matches_word_forms_and_yo():
    # ё -> е и сравнение по основам слов: «Лёгкий бег» находит «легкий бег»
    assert expand_terms(["Лёгкий бег"]) == [group_with("Л-темп")]
    assert expand_terms(["интервал"]) == [group_with("И-темп")]  # «интервал» и «интервалы»: одна основа
    assert expand_terms(["интервалы", "МПК"]) == [group_with("И-темп")]


def test_expand_terms_skips_empty_and_duplicates():
    assert expand_terms(["  ", "травма", "Травма", "боль"]) == [group_with("травма")]
    assert expand_terms(["ахилл", "ахилл "]) == [["ахилл"]]


def test_fallback_terms_drop_stopwords_and_short_words():
    terms = fallback_terms("Почему нужно бегать лёгкие пробежки так медленно, если я спешу?")
    assert terms == ["бегать", "лёгкие", "пробежки", "медленно", "спешу"]
    assert len(fallback_terms(" ".join(f"слово{i}" for i in range(20)))) == MAX_SEARCH_TERMS


def test_tidy_heading_lowercases_caps_but_keeps_latin_abbreviations():
    assert tidy_heading("Глава 5. СИСТЕМА ТРЕНИРОВОК VDOT") == "Глава 5. Система тренировок VDOT"
    assert tidy_heading("ЗАПЛАНИРОВАННЫЕ ПЕРЕРЫВЫ") == "Запланированные перерывы"
    assert tidy_heading("Глава 1. Учимся замедляться") == "Глава 1. Учимся замедляться"
    assert tidy_heading("") == ""


def test_short_author():
    assert short_author("Джек Дэниелс") == "Дэниелс"
    assert short_author("Мэт Фицджеральд, Бен Розарио") == "Фицджеральд, Розарио"


FRAGMENTS = [
    Fragment("Джек Дэниелс", "От 800 метров до марафона", "Глава 9. ПЕРЕРЫВЫ", "ЗАПЛАНИРОВАННЫЕ ПЕРЕРЫВЫ", "Текст 1"),
    Fragment("Мэт Фицджеральд", "Бег по правилу 80/20", "Глава 1. Учимся <замедляться>", None, "Текст 2"),
]


def test_format_fragments_numbers_sources():
    text = format_fragments(FRAGMENTS)
    assert text.startswith("[1] Дэниелс, «От 800 метров до марафона», Глава 9. Перерывы / Запланированные перерывы\n")
    assert "\n\n[2] Фицджеральд, «Бег по правилу 80/20», Глава 1. Учимся <замедляться>\nТекст 2" in text


def test_cited_numbers_order_unique_and_in_range():
    answer = "Лёгкий бег [2]. После перерыва [1][2], см. также [7] и [0]."
    assert cited_numbers(answer, available=2) == [2, 1]
    assert cited_numbers("без ссылок", available=2) == []


def test_render_sources_only_cited_and_escaped():
    text = render_sources(FRAGMENTS, [2])
    assert text == "📚 <b>Источники</b>\n[2] Фицджеральд, «Бег по правилу 80/20» — Глава 1. Учимся &lt;замедляться&gt;"
    # раздел отличает фрагменты одной главы
    assert render_sources(FRAGMENTS, [1]).endswith("— Глава 9. Перерывы / Запланированные перерывы")
    assert render_sources(FRAGMENTS, []) == ""


def test_plan_context_current_week():
    macro = MacroPlan.model_validate(valid_macro())
    week = WeekPlan.model_validate({"days": valid_week_days()})
    text = plan_context(
        target_race="Полумарафон (21.1 км)", race_date=date(2026, 12, 6), today=date(2026, 10, 9),
        total_weeks=12, week_number=2, macro=macro, week=week,
    )
    lines = text.splitlines()
    assert lines[0] == "Цель: Полумарафон (21.1 км), забег 06.12.2026 (дней до забега: 58)"
    assert lines[1].startswith(f"Неделя 2 из 12, фаза 1 «{macro.phases[0].name}»")
    assert f"План текущей недели ({week.total_km:g} км):" in lines
    assert "- Вт: пороговая 8 км (рабочая часть 3 км), 3 км в темпе T" in lines
    assert "- Пн: отдых" in lines


def test_plan_context_before_start_and_after_race():
    before = plan_context(
        target_race="10 км", race_date=date(2026, 12, 6), today=date(2026, 10, 9),
        total_weeks=8, week_number=None, macro=None, week=None,
    )
    assert "ещё не начался" in before
    after = plan_context(
        target_race="10 км", race_date=date(2026, 10, 1), today=date(2026, 10, 9),
        total_weeks=8, week_number=None, macro=None, week=None,
    )
    assert after.endswith("Забег уже прошёл, план завершён.")

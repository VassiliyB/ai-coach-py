"""Вопросы к тренеру (/ask): чистые функции без I/O.

Поток: модель превращает вопрос в слова поиска, код дополняет их синонимами из переводов книг
(expand_terms), BookRepository.search находит фрагменты, модель отвечает со ссылками [n],
а список источников под ответом собирает код (render_sources): название книги и главы модель не пишет.
"""
import html
import re
from dataclasses import dataclass
from datetime import date
from typing import Iterable, List, Optional, Sequence

from pydantic import BaseModel, Field

from schemas.plan import MacroPlan, WeekPlan, WorkoutType

MAX_QUESTION_CHARS = 1000
MAX_SEARCH_TERMS = 8
SEARCH_LIMIT = 6

# Одно понятие разными словами: у Дэниелса в переводе буквенные темпы (Л/М/П/И/Пв, Д-бег),
# у Фицджеральда зоны 1–5 и «длинная пробежка». Понятие из группы ищется всеми её вариантами,
# а в ранжировании считается одним совпадением (см. BookRepository.search)
SYNONYM_GROUPS: List[List[str]] = [
    ["легкий бег", "Л-темп", "Л-бег", "разговорный темп", "низкая интенсивность", "зона 1", "зона 2"],
    ["восстановительная пробежка", "восстановительный бег", "трусца"],
    ["длительный бег", "длительная пробежка", "длинная пробежка", "Д-бег"],
    ["марафонский темп", "М-темп", "М-бег"],
    ["пороговый бег", "П-темп", "темповый бег", "крейсерские интервалы", "лактатный порог", "зона 3"],
    ["интервалы", "И-темп", "интервальная тренировка", "МПК", "зона 4"],
    ["повторы", "Пв-темп", "Пв-тренировка", "ускорения", "зона 5"],
    ["умеренная интенсивность", "серая зона", "зона X", "колея умеренной интенсивности"],
    ["качественная тренировка", "К-тренировка", "К-сессия", "интенсивная тренировка"],
    ["перерыв", "пропуск тренировок", "возвращение к тренировкам"],
    ["травма", "боль", "травмирование"],
    ["пульс", "ЧСС", "частота сердечных сокращений"],
    ["ОФП", "силовая тренировка", "упражнения на технику", "плиометрика"],
    ["кросс-тренинг", "перекрестная тренировка", "велосипед", "плавание"],
    ["километраж", "объем бега", "недельный объем"],
    ["разгрузочная неделя", "снижение нагрузки"],
    # у Дэниелса «периоды отдыха», «периоды восстановления», «короткие паузы»; «трусцой» не брать:
    # оно в каждом плане («2 мин трусцой») и вытесняет объяснения
    ["отдых между отрезками", "восстановление между отрезками", "период отдыха", "период восстановления",
     "короткая пауза"],
]
# Названия понятий для модели на шаге поиска: первое слово каждой группы
CONCEPT_NAMES = ", ".join(f"«{group[0]}»" for group in SYNONYM_GROUPS)

# Для запасного поиска по словам вопроса: служебные слова ничего не находят
_STOPWORDS = {
    "почему", "зачем", "когда", "сколько", "какой", "какая", "какие", "каким", "какую", "можно", "нужно",
    "надо", "если", "чтобы", "этот", "это", "эта", "эти", "меня", "мне", "мой", "моя", "мои", "свой",
    "своих", "тебя", "лучше", "очень", "после", "перед", "между", "время", "делать", "есть", "будет",
    "было", "или", "как", "что", "для", "при", "без", "над", "под", "так", "уже", "еще", "ещё", "только",
}
_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9-]*")
_CITE_RE = re.compile(r"\[(\d{1,2})\]")
# Длина основы для сравнения слов: «легкий»/«легкие», «пробежка»/«пробежки» совпадают
_STEM_LEN = 5

TYPE_NAMES = {
    WorkoutType.REST: "отдых",
    WorkoutType.EASY: "лёгкий бег",
    WorkoutType.LONG: "длительный бег",
    WorkoutType.MARATHON: "марафонский темп",
    WorkoutType.THRESHOLD: "пороговая",
    WorkoutType.INTERVAL: "интервалы",
    WorkoutType.REPETITION: "повторы",
    WorkoutType.CROSS: "ОФП / кросс-тренинг",
}
DAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


class SearchTerms(BaseModel):
    """Ответ модели на шаге поиска: слова и короткие фразы, по которым искать в книгах."""
    terms: List[str] = Field(min_length=1, max_length=MAX_SEARCH_TERMS * 2)


@dataclass(frozen=True)
class Fragment:
    """Найденный фрагмент книги в виде, удобном для промпта и списка источников."""
    author: str
    title: str
    chapter: str
    section: Optional[str]
    content: str


def _normalize(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").split())


def _stems(text: str) -> tuple:
    return tuple(word[:_STEM_LEN] for word in _normalize(text).split())


def expand_terms(terms: Iterable[str]) -> List[List[str]]:
    """Слова поиска -> группы вариантов. Слово из словаря синонимов разворачивается в свою группу,
    остальные остаются группой из одного слова. Повторы групп убираются, порядок сохраняется."""
    groups: List[List[str]] = []
    seen = set()
    for term in terms:
        term = " ".join(term.split())
        if not term:
            continue
        stems = _stems(term)
        group = next((g for g in SYNONYM_GROUPS if any(_stems(v) == stems for v in g)), None)
        key = tuple(group) if group else (_normalize(term),)
        if key in seen:
            continue
        seen.add(key)
        groups.append(list(group) if group else [term])
    return groups


def fallback_terms(question: str) -> List[str]:
    """Слова вопроса для поиска, если модель не вернула слова: без служебных и коротких слов."""
    words = []
    for word in _WORD_RE.findall(question):
        if len(word) >= 4 and _normalize(word) not in _STOPWORDS and word not in words:
            words.append(word)
    return words[:MAX_SEARCH_TERMS]


def tidy_heading(text: str) -> str:
    """«Глава 9. ПЕРЕРЫВЫ В ТРЕНИРОВКАХ И VDOT» -> «Глава 9. Перерывы в тренировках и VDOT».
    Заголовки Дэниелса в EPUB набраны прописными, а «Глава N.» к ним добавил разбор книги;
    латинские аббревиатуры (VDOT) оставляем."""
    prefix_match = re.match(r"(Глава \d+\.\s*)(.*)", text or "")
    prefix, title = prefix_match.groups() if prefix_match else ("", text or "")
    if not title.isupper():
        return text
    words = [w if re.fullmatch(r"[A-Z0-9-]+", w) else w.lower() for w in title.split()]
    result = " ".join(words)
    return prefix + result[:1].upper() + result[1:]


def short_author(author: str) -> str:
    """«Мэт Фицджеральд, Бен Розарио» -> «Фицджеральд, Розарио»."""
    return ", ".join(part.split()[-1] for part in author.split(",") if part.split())


def format_fragments(fragments: Sequence[Fragment]) -> str:
    """Фрагменты для промпта: номер [n], источник и текст. Весь блок идёт внутри <data>."""
    parts = []
    for number, fragment in enumerate(fragments, 1):
        parts.append(
            f"[{number}] {short_author(fragment.author)}, «{fragment.title}», {_where(fragment)}\n{fragment.content}"
        )
    return "\n\n".join(parts)


def cited_numbers(answer: str, available: int) -> List[int]:
    """Номера источников [n], на которые сослался ответ, по порядку первого упоминания."""
    numbers: List[int] = []
    for match in _CITE_RE.finditer(answer):
        number = int(match.group(1))
        if 1 <= number <= available and number not in numbers:
            numbers.append(number)
    return numbers


def render_sources(fragments: Sequence[Fragment], numbers: Sequence[int]) -> str:
    """Список источников под ответом (HTML). Только те, на которые модель сослалась."""
    if not numbers:
        return ""
    lines = ["📚 <b>Источники</b>"]
    for number in numbers:
        fragment = fragments[number - 1]
        lines.append(
            f"[{number}] {html.escape(short_author(fragment.author))}, «{html.escape(fragment.title)}» — "
            f"{html.escape(_where(fragment))}"
        )
    return "\n".join(lines)


def _where(fragment: Fragment) -> str:
    """Глава и раздел: несколько фрагментов одной главы различаются разделом."""
    where = tidy_heading(fragment.chapter)
    if fragment.section:
        where += f" / {tidy_heading(fragment.section)}"
    return where


def _day_text(day_index: int, day) -> str:
    text = f"{DAY_NAMES[day_index]}: {TYPE_NAMES.get(day.type, day.type.value)}"
    if day.distance_km:
        text += f" {day.distance_km:g} км"
    if day.quality_km:
        text += f" (рабочая часть {day.quality_km:g} км)"
    if day.description.strip():
        text += f", {' '.join(day.description.split())}"
    return text


def plan_context(
    *,
    target_race: str,
    race_date: date,
    today: date,
    total_weeks: int,
    week_number: Optional[int],
    macro: Optional[MacroPlan],
    week: Optional[WeekPlan],
) -> str:
    """Текущий план атлета для ответа на вопрос. Описания дней написала модель при генерации недели,
    поэтому текст целиком идёт в <data>, а не в инструкции."""
    days_left = (race_date - today).days
    lines = [f"Цель: {target_race}, забег {race_date:%d.%m.%Y} (дней до забега: {max(days_left, 0)})"]
    if days_left < 0:
        lines.append("Забег уже прошёл, план завершён.")
        return "\n".join(lines)
    if week_number is None:
        lines.append(f"План из {total_weeks} нед. ещё не начался: сейчас вводные дни с лёгким бегом.")
    else:
        line = f"Неделя {week_number} из {total_weeks}"
        if macro is not None:
            phase = macro.phase_for_week(week_number)
            line += f", фаза {phase.number} «{phase.name}»"
            if phase.focus.strip():
                line += f": {' '.join(phase.focus.split())}"
        lines.append(line)
    if week is not None:
        lines.append(f"План текущей недели ({week.total_km:g} км):")
        lines.extend(f"- {_day_text(i, day)}" for i, day in enumerate(week.days))
    return "\n".join(lines)

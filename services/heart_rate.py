# services/heart_rate.py
"""Пульсовые зоны: от ЧССmax (Дэниелс) или от пульса ПАНО (Фицджеральд). Чистые функции, без I/O.

ЧССmax из Garmin это пик по пробежкам за 90 дней, а не настоящий максимум: зоны от него приблизительные.
Пользователь может задать максимум или пульс ПАНО вручную (/pulse). Приоритет: ПАНО, ручной максимум,
пик Garmin. ПАНО точнее: Фицджеральд строит зоны от порога, а ЧССmax без теста почти всегда неточен.
"""
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, Union

KIND_MAX = "max"
KIND_LTHR = "lthr"

# Доли от ЧССmax (sports_knowledge.txt). Для зоны R пульс не задаётся: отрезки слишком короткие,
# пульс не успевает выйти на плато.
HR_FRACTIONS: Dict[str, Tuple[float, float]] = {
    "E": (0.65, 0.79),
    "M": (0.80, 0.85),
    "T": (0.88, 0.92),
    "I": (0.95, 1.00),
}
# Доли от пульса ПАНО по зонам Фицджеральда (sports_knowledge.txt): E = зоны 1–2, M = зона X,
# T = зона 3, I = зона 4. При ПАНО ≈ 89% ЧССmax диапазоны близки к HR_FRACTIONS.
LTHR_FRACTIONS: Dict[str, Tuple[float, float]] = {
    "E": (0.75, 0.89),
    "M": (0.90, 0.95),
    "T": (0.96, 1.00),
    "I": (1.02, 1.05),
}

MIN_MAX_HR, MAX_MAX_HR = 140, 230
MIN_LTHR, MAX_LTHR = 120, 210
LTHR_MAX_SHARE = (0.75, 0.97)    # ПАНО относительно ручного ЧССmax: вне этого вероятна ошибка ввода

_MAX_WORDS = {"max", "макс", "максимум", "чссmax", "чсс", "hrmax"}
_LTHR_WORDS = {"пано", "порог", "lthr", "лп"}
RESET_WORDS = {"сброс", "сбросить", "reset", "garmin"}
_INPUT_RE = re.compile(r"^\s*([^\d\s]+)?\s*(\d{2,3})\s*$")


@dataclass(frozen=True)
class HeartRateBasis:
    bpm: int
    kind: str = KIND_MAX
    manual: bool = False

    @property
    def fractions(self) -> Dict[str, Tuple[float, float]]:
        return LTHR_FRACTIONS if self.kind == KIND_LTHR else HR_FRACTIONS

    @property
    def label(self) -> str:
        """'ЧССmax 188' или 'пульса ПАНО 172': продолжение фразы «N% от ...»."""
        return f"пульса ПАНО {self.bpm}" if self.kind == KIND_LTHR else f"ЧССmax {self.bpm}"

    @property
    def source_text(self) -> str:
        if self.kind == KIND_LTHR:
            return f"пульс ПАНО <b>{self.bpm}</b> (задан вручную)"
        if self.manual:
            return f"ЧССmax <b>{self.bpm}</b> (задан вручную)"
        return f"ЧССmax <b>{self.bpm}</b> (пик по пробежкам Garmin за 90 дней)"

    def range_for_zone(self, zone: Optional[str]) -> Optional[Tuple[int, int]]:
        fractions = self.fractions.get(zone or "")
        if not fractions:
            return None
        return round(self.bpm * fractions[0]), round(self.bpm * fractions[1])

    def zone_of(self, hr: Optional[float]) -> Optional[Tuple[str, int]]:
        """(зона, процент от базы) по пульсу. Промежутки между диапазонами относятся к зоне ниже."""
        if not hr:
            return None
        fraction = hr / self.bpm
        zone = "below_E"
        for name in ("E", "M", "T", "I"):
            if fraction >= self.fractions[name][0]:
                zone = name
        return zone, round(fraction * 100)


# Число это ЧССmax (так пульс передавался раньше и передаётся в тестах)
HrRef = Union[HeartRateBasis, int, None]


def as_basis(ref: HrRef) -> Optional[HeartRateBasis]:
    if isinstance(ref, HeartRateBasis):
        return ref if ref.bpm > 0 else None
    if ref and ref > 0:
        return HeartRateBasis(int(ref))
    return None


def profile_hr_basis(profile: Optional[Any]) -> Optional[HeartRateBasis]:
    """База пульсовых зон атлета: ПАНО, ручной ЧССmax или пик Garmin. None, если ничего нет."""
    lthr = getattr(profile, "lthr", None)
    if lthr:
        return HeartRateBasis(lthr, KIND_LTHR, manual=True)
    manual_max = getattr(profile, "manual_max_hr", None)
    if manual_max:
        return HeartRateBasis(manual_max, KIND_MAX, manual=True)
    peak = getattr(profile, "max_heart_rate", None)
    return HeartRateBasis(peak) if peak else None


@dataclass(frozen=True)
class PulseInput:
    kind: Optional[str]          # KIND_MAX / KIND_LTHR; None: сброс ручных значений
    bpm: Optional[int] = None


def parse_pulse(text: str) -> Optional[PulseInput]:
    """'188', 'max 188', 'пано 172', 'сброс'. Число без слова это ЧССmax. None, если ввод не распознан."""
    text = (text or "").strip().lower().replace("ё", "е")
    if text in RESET_WORDS:
        return PulseInput(None)
    match = _INPUT_RE.match(text)
    if not match:
        return None
    word, bpm = (match.group(1) or "").strip(":=-"), int(match.group(2))
    if not word or word in _MAX_WORDS:
        return PulseInput(KIND_MAX, bpm)
    if word in _LTHR_WORDS:
        return PulseInput(KIND_LTHR, bpm)
    return None


def check_pulse(value: PulseInput, manual_max: Optional[int], lthr: Optional[int]) -> Optional[str]:
    """Почему значение не принято (текст для пользователя) или None. manual_max, lthr: уже сохранённые."""
    if value.kind == KIND_MAX:
        if not MIN_MAX_HR <= value.bpm <= MAX_MAX_HR:
            return f"ЧССmax должен быть от {MIN_MAX_HR} до {MAX_MAX_HR} уд/мин."
        if lthr and not _lthr_fits(lthr, value.bpm):
            return f"ЧССmax {value.bpm} не согласуется с сохранённым пульсом ПАНО {lthr}. Проверьте оба значения."
    elif value.kind == KIND_LTHR:
        if not MIN_LTHR <= value.bpm <= MAX_LTHR:
            return f"Пульс ПАНО должен быть от {MIN_LTHR} до {MAX_LTHR} уд/мин."
        if manual_max and not _lthr_fits(value.bpm, manual_max):
            return (
                f"Пульс ПАНО {value.bpm} не согласуется с ЧССmax {manual_max}: обычно ПАНО "
                f"{LTHR_MAX_SHARE[0]:.0%}–{LTHR_MAX_SHARE[1]:.0%} от максимума."
            )
    return None


def _lthr_fits(lthr: int, max_hr: int) -> bool:
    return LTHR_MAX_SHARE[0] <= lthr / max_hr <= LTHR_MAX_SHARE[1]


ZONE_LABELS = {"E": "E · лёгкий", "M": "M · марафонский", "T": "T · пороговый", "I": "I · интервалы"}

PULSE_HINT = (
    "ПАНО примерно равен среднему пульсу последних 20 минут 30-минутного забега или теста на пределе. "
    "ЧССmax лучше брать из теста или финиша забега, а не по формуле от возраста."
)


def pulse_help(manual_max: Optional[int] = None, lthr: Optional[int] = None) -> str:
    """Как задать пульс вручную (HTML). Числа в примерах это образец ввода, поэтому рядом текущие значения."""
    def now(value: Optional[int]) -> str:
        return f" (сейчас {value})" if value else ""

    return (
        "Задать вручную, например:\n"
        f"• <code>/pulse пано 172</code> — пульс ПАНО (лактатный порог), самый точный вариант{now(lthr)};\n"
        f"• <code>/pulse max 188</code> — максимальный пульс{now(manual_max)};\n"
        "• <code>/pulse сброс</code> — вернуться к пику по пробежкам Garmin.\n\n"
        f"{PULSE_HINT}"
    )


def render_pulse(
    basis: Optional[HeartRateBasis], manual_max: Optional[int] = None, lthr: Optional[int] = None,
    with_help: bool = True,
) -> str:
    """Источник пульсовых зон и сами зоны (HTML, все числа от кода). with_help=False: после сохранения,
    без инструкции, чтобы примеры ввода не путались с только что заданным значением."""
    help_text = pulse_help(manual_max, lthr) if with_help else "Изменить: <code>/pulse</code>"
    if basis is None:
        return "❤️ Пульсовые зоны не заданы: нет данных пульса из Garmin.\n\n" + help_text
    lines = [f"❤️ <b>Пульсовые зоны</b> от: {basis.source_text}"]
    for zone, label in ZONE_LABELS.items():
        low, high = basis.range_for_zone(zone)
        lines.append(f"{label}: <code>{low}–{high} уд/мин</code>")
    lines.append("R · повторы: по темпу, пульс не успевает выйти на плато")
    if basis.kind == KIND_LTHR and manual_max:
        lines.append(f"\nЧССmax {manual_max} сохранён, но зоны считаются от ПАНО: он точнее.")
    return "\n".join(lines) + "\n\n" + help_text

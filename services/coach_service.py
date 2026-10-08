# services/coach_service.py
"""Формулы Дэниелса: VDOT, зоны темпа, прогноз времени. Чистые функции, без I/O."""
import math
from dataclasses import dataclass
from typing import Any, Dict, Optional

MIN_VDOT, MAX_VDOT = 20.0, 85.0

# Интенсивность зон как доля VO2max (приближение к таблицам Дэниелса)
EASY_SLOW_PCT, EASY_FAST_PCT = 0.59, 0.74
THRESHOLD_PCT = 0.88
INTERVAL_PCT = 0.98
REPETITION_PCT = 1.05

MARATHON_M = 42195.0


def _vo2_at_velocity(v_m_min: float) -> float:
    return -4.60 + 0.182258 * v_m_min + 0.000104 * v_m_min ** 2


def _fraction_of_vo2max(t_min: float) -> float:
    return 0.8 + 0.1894393 * math.exp(-0.012778 * t_min) + 0.2989558 * math.exp(-0.1932605 * t_min)


def calculate_vdot(distance_m: float, time_s: float) -> float:
    """VDOT по результату забега (формула Дэниелса-Гилберта)."""
    if distance_m <= 0 or time_s <= 0:
        raise ValueError("Дистанция и время должны быть положительными")
    t_min = time_s / 60.0
    return _vo2_at_velocity(distance_m / t_min) / _fraction_of_vo2max(t_min)


def _velocity_at_vo2(vo2: float) -> float:
    """Обратная функция: скорость (м/мин) при заданном потреблении кислорода."""
    a, b, c = 0.000104, 0.182258, -(4.60 + vo2)
    return (-b + math.sqrt(b * b - 4 * a * c)) / (2 * a)


def _pace_at_fraction(vdot: float, fraction: float) -> float:
    """Темп в секундах на км при заданной доле от VO2max."""
    return 1000.0 / _velocity_at_vo2(vdot * fraction) * 60.0


def predict_race_time(vdot: float, distance_m: float) -> float:
    """Прогноз времени (сек) на дистанции: бисекция по монотонной calculate_vdot."""
    lo, hi = 1.0, 600.0  # минуты
    for _ in range(60):
        mid = (lo + hi) / 2
        if calculate_vdot(distance_m, mid * 60) > vdot:
            lo = mid  # слишком быстро → нужно больше времени
        else:
            hi = mid
    return (lo + hi) / 2 * 60.0


def format_pace(sec_per_km: float) -> str:
    total = int(round(sec_per_km))
    return f"{total // 60}:{total % 60:02d}"


@dataclass(frozen=True)
class PaceRange:
    fast: float  # сек/км
    slow: float  # сек/км

    def __str__(self) -> str:
        if abs(self.slow - self.fast) < 1:
            return f"{format_pace(self.fast)} /км"
        return f"{format_pace(self.fast)}–{format_pace(self.slow)} /км"


@dataclass(frozen=True)
class TrainingZones:
    vdot: float
    easy: PaceRange
    marathon: PaceRange
    threshold: PaceRange
    interval: PaceRange
    repetition: PaceRange


def calculate_zones(vdot: float) -> TrainingZones:
    if not (MIN_VDOT <= vdot <= MAX_VDOT):
        raise ValueError(f"VDOT {vdot:.1f} вне допустимого диапазона {MIN_VDOT}–{MAX_VDOT}")

    def single(pct: float) -> PaceRange:
        p = _pace_at_fraction(vdot, pct)
        return PaceRange(p, p)

    marathon_pace = predict_race_time(vdot, MARATHON_M) / (MARATHON_M / 1000.0)
    return TrainingZones(
        vdot=round(vdot, 1),
        easy=PaceRange(fast=_pace_at_fraction(vdot, EASY_FAST_PCT), slow=_pace_at_fraction(vdot, EASY_SLOW_PCT)),
        marathon=PaceRange(marathon_pace, marathon_pace),
        threshold=single(THRESHOLD_PCT),
        interval=single(INTERVAL_PCT),
        repetition=single(REPETITION_PCT),
    )


def format_zones(zones: TrainingZones) -> str:
    return (
        f"РАССЧИТАННЫЕ ЗОНЫ ТЕМПА (VDOT {zones.vdot}):\n"
        f"- E (легкий): {zones.easy}\n"
        f"- M (марафонский): {zones.marathon}\n"
        f"- T (пороговый): {zones.threshold}\n"
        f"- I (интервалы МПК): {zones.interval}\n"
        f"- R (повторы): {zones.repetition}"
    )


def zones_for_profile(profile: Optional[Any]) -> Optional[TrainingZones]:
    """Зоны темпа по VDOT из профиля. None, если профиля нет или VDOT не рассчитан (нужен /sync)."""
    vdot = getattr(profile, "vdot", None)
    if not vdot or not (MIN_VDOT <= vdot <= MAX_VDOT):
        return None
    return calculate_zones(vdot)


def build_profile_context(profile: Optional[Any]) -> Dict[str, Any]:
    """Профиль из БД -> словарь для промптов (текст паспорта + готовые зоны)."""
    if profile is None:
        return {}
    ctx: Dict[str, Any] = {"summary_text": profile.raw_summary_text}
    zones = zones_for_profile(profile)
    if zones is not None:
        ctx["zones_text"] = format_zones(zones)
    return ctx
# services/plan_renderer.py
"""Сборка сообщений Telegram (HTML) из структурированных планов. Модель в этом не участвует:
темп и пульс подставляет код, весь текст от модели экранируется."""
import html
from datetime import date
from typing import List, Optional

from schemas.plan import MacroPlan, Phase, PlannedDay, WeekPlan, WorkoutType
from services.coach_service import TrainingZones
from services.plan_paces import estimate_duration_min, hr_text, pace_text

DAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
SEP = " · "

TYPE_LABELS = {
    WorkoutType.REST: "Отдых",
    WorkoutType.EASY: "Лёгкий бег",
    WorkoutType.LONG: "Длительный бег",
    WorkoutType.MARATHON: "Марафонский темп",
    WorkoutType.THRESHOLD: "Пороговая",
    WorkoutType.INTERVAL: "Интервалы",
    WorkoutType.REPETITION: "Повторы",
    WorkoutType.CROSS: "ОФП / растяжка",
}
TYPE_ICONS = {
    WorkoutType.REST: "😴",
    WorkoutType.EASY: "🟢",
    WorkoutType.LONG: "🔵",
    WorkoutType.MARATHON: "🟡",
    WorkoutType.THRESHOLD: "🟠",
    WorkoutType.INTERVAL: "🔴",
    WorkoutType.REPETITION: "🟣",
    WorkoutType.CROSS: "🧘",
}


def _e(value: object) -> str:
    """Экранирует текст для Telegram HTML."""
    return html.escape(str(value), quote=False)


def _km(value: float) -> str:
    return f"{value:g}"


def render_zones(zones: TrainingZones) -> str:
    return "\n".join([
        f"⚡ <b>Ваши зоны темпа</b> (VDOT {zones.vdot:g})",
        f"E · лёгкий: <code>{zones.easy}</code>",
        f"M · марафонский: <code>{zones.marathon}</code>",
        f"T · пороговый: <code>{zones.threshold}</code>",
        f"I · интервалы: <code>{zones.interval}</code>",
        f"R · повторы: <code>{zones.repetition}</code>",
    ])


def _render_day(day: PlannedDay, zones: Optional[TrainingZones], max_hr: Optional[int]) -> str:
    label = TYPE_LABELS[day.type]
    head = f"{TYPE_ICONS[day.type]} <b>{DAY_NAMES[day.day - 1]}</b>{SEP}{label}"
    if day.distance_km:
        head += f"{SEP}{_km(day.distance_km)} км"
        if day.quality_km:
            head += f" (рабочая часть {_km(day.quality_km)} км)"
    lines = [head]

    details: List[str] = []
    duration = estimate_duration_min(day, zones)
    if duration:
        details.append(f"⏱ ≈{duration} мин")
    pace = pace_text(day, zones)
    if pace:
        suffix = " (рабочая часть)" if day.quality_km else ""
        details.append(f"темп <code>{pace}</code>{suffix}")
    hr = hr_text(day, max_hr)
    if hr:
        details.append(f"пульс <code>{hr}</code>")
    if details:
        lines.append("   " + SEP.join(details))

    description = day.description.strip()
    if description and description.lower() != label.lower():
        lines.append(f"   <i>{_e(description)}</i>")
    return "\n".join(lines)


def render_week(
    week: WeekPlan,
    *,
    target_race: str,
    week_number: int,
    total_weeks: int,
    week_start: str,
    week_end: str,
    phase: Optional[Phase] = None,
    zones: Optional[TrainingZones] = None,
    max_hr: Optional[int] = None,
) -> str:
    """Недельное расписание. week_start и week_end приходят уже отформатированными строками."""
    lines = [
        f"📋 <b>Неделя №{week_number} из {total_weeks}</b>{SEP}{_e(week_start)} – {_e(week_end)}",
        f"🎯 Цель: <b>{_e(target_race)}</b>",
    ]
    if phase:
        lines.append(f"Фаза {phase.number}: <b>{_e(phase.name)}</b>")

    summary = f"Объём: <b>{_km(week.total_km)} км</b>"
    quality = round(sum(d.quality_km or 0.0 for d in week.days), 1)
    if quality:
        summary += f"{SEP}рабочая часть качественных: {_km(quality)} км"
    lines += [summary, ""]

    blocks = [_render_day(d, zones, max_hr) for d in week.days]
    lines.append("\n\n".join(blocks))

    if week.note.strip():
        lines += ["", f"💬 <i>{_e(week.note.strip())}</i>"]
    if zones is None:
        lines += ["", "ℹ️ Темпы не показаны: выполните /sync, чтобы рассчитать VDOT."]
    return "\n".join(lines)


def render_macro(
    macro: MacroPlan,
    target_race: str,
    race_date: Optional[date] = None,
    zones: Optional[TrainingZones] = None,
) -> str:
    """Макроцикл: фазы с диапазонами недель, километраж по неделям, зоны темпа."""
    summary = []
    if race_date:
        summary.append(f"Забег: {race_date.strftime('%d.%m.%Y')}")
    summary += [f"{macro.total_weeks} нед.", f"пик {_km(max(macro.weekly_km))} км/нед"]

    lines = [f"🎯 <b>План подготовки: {_e(target_race)}</b>", SEP.join(summary), ""]
    if zones is not None:
        lines += [render_zones(zones), ""]

    start = 1
    for phase in macro.phases:
        if phase.weeks == 0:
            continue
        end = start + phase.weeks - 1
        weeks_range = f"нед. {start}" if start == end else f"нед. {start}–{end}"
        lines.append(f"<b>Фаза {phase.number} · {_e(phase.name)}</b> ({weeks_range})")
        if phase.focus.strip():
            lines.append(f"<i>{_e(phase.focus.strip())}</i>")
        km = macro.weekly_km[start - 1:end]
        lines.append("Километраж: " + SEP.join(_km(v) for v in km) + " км")
        lines.append("")
        start = end + 1

    if macro.notes:
        lines.append("⚠️ <b>Важно</b>")
        lines += [f"• {_e(note)}" for note in macro.notes]
    return "\n".join(lines).strip()
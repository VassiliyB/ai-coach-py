# services/plan_renderer.py
"""Сборка сообщений Telegram (HTML) из структурированных планов. Модель в этом не участвует:
темп и пульс подставляет код, весь текст от модели экранируется."""
import html
from datetime import date
from typing import List, Optional

from schemas.plan import MacroPlan, Phase, PlannedDay, WeekPlan, WorkoutType
from services.coach_service import TrainingZones
from services.heart_rate import HrRef
from services.macro_replan import Replan
from services.plan_paces import estimate_duration_min, hr_text, pace_text
from services.vdot_review import VdotChange, VdotReview
from services.week_adaptation import WeekAdjustment

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


def _render_day(day: PlannedDay, zones: Optional[TrainingZones], hr_basis: HrRef) -> str:
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
    hr = hr_text(day, hr_basis)
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
    hr_basis: HrRef = None,
    adjustment: Optional[WeekAdjustment] = None,
) -> str:
    """Недельное расписание. week_start и week_end приходят уже отформатированными строками.

    adjustment: поправка по факту прошлой недели; показывается, если она что-то изменила.
    """
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

    if adjustment is not None and (adjustment.changed or adjustment.summary):
        if adjustment.summary:
            lines.append(f"📊 {_e(adjustment.summary)}")
        if adjustment.changed:
            lines.append("🔄 <b>Корректировка:</b>")
            lines += [f"• {_e(reason)}" for reason in adjustment.reasons]
        lines.append("")

    blocks = [_render_day(d, zones, hr_basis) for d in week.days]
    lines.append("\n\n".join(blocks))

    if week.note.strip():
        lines += ["", f"💬 <i>{_e(week.note.strip())}</i>"]
    if zones is None:
        lines += ["", "ℹ️ Темпы не показаны: выполните /sync, чтобы рассчитать VDOT."]
    return "\n".join(lines)


def render_intro_days(
    week: WeekPlan,
    days: List[date],
    *,
    zones: Optional[TrainingZones] = None,
    hr_basis: HrRef = None,
) -> str:
    """Вводные дни до старта плана: показываются только days, прошедшие дни недели (rest) скрыты."""
    active = {d.isoweekday() for d in days}
    shown = [d for d in week.days if d.day in active]
    lines = [
        f"🔜 <b>До старта плана</b>{SEP}{days[0]:%d.%m} – {days[-1]:%d.%m}",
        "Неделя №1 начнётся в понедельник, а пока база: только лёгкий бег.",
        f"Объём: <b>{_km(round(sum(d.distance_km or 0.0 for d in shown), 1))} км</b>",
        "",
        "\n\n".join(_render_day(d, zones, hr_basis) for d in shown),
    ]
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
    goal_line: Optional[str] = None,
) -> str:
    """Макроцикл: фазы с диапазонами недель, километраж по неделям, зоны темпа.

    goal_line: готовая строка о цели (race_goal.render_goal_line), уже в HTML.
    """
    summary = []
    if race_date:
        summary.append(f"Забег: {race_date.strftime('%d.%m.%Y')}")
    summary += [f"{macro.total_weeks} нед.", f"пик {_km(max(macro.weekly_km))} км/нед"]

    lines = [f"🎯 <b>План подготовки: {_e(target_race)}</b>", SEP.join(summary), ""]
    if goal_line:
        lines += [goal_line, ""]
    if zones is not None:
        lines += [render_zones(zones)]
        if goal_line:
            lines.append("<i>Темпы тренировок по текущей форме, а не по цели: обновятся после /sync.</i>")
        lines.append("")

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


def render_plan_overview(
    *,
    target_race: str,
    race_date: date,
    today: date,
    total_weeks: int,
    week_number: Optional[int],
    start_monday: date,
    macro: Optional[MacroPlan] = None,
    goal_line: Optional[str] = None,
    forecast_line: Optional[str] = None,
) -> str:
    """Обзор активного плана для /show_plan.

    week_number: текущая неделя (None до начала плана или после забега).
    goal_line и forecast_line: готовые строки (HTML) от race_goal, все числа посчитаны кодом.
    """
    days_left = (race_date - today).days
    if days_left > 0:
        when = f"через {days_left} дн."
    elif days_left == 0:
        when = "сегодня!"
    else:
        when = "прошёл"
    lines = [
        f"📋 <b>План: {_e(target_race)}</b>",
        f"🏁 Забег: <b>{race_date:%d.%m.%Y}</b>{SEP}{when}",
        f"📆 Всего недель: {total_weeks}",
    ]

    if days_left < 0:
        lines.append("План завершён. Новый план: /plan")
    elif week_number is None:
        lines.append("Сейчас: <b>вводные дни</b> (лёгкий бег до старта плана)")
        lines.append(f"Неделя №1 начнётся в понедельник {start_monday:%d.%m}.")
        if macro is not None:
            first = next(p for p in macro.phases if p.weeks)
            lines.append(f"Первый период: Фаза {first.number} · {_e(first.name)}")
    else:
        lines.append(f"Сейчас: <b>неделя {week_number} из {total_weeks}</b>")
        if macro is not None:
            phase = macro.phase_for_week(week_number)
            lines.append(f"Период: <b>Фаза {phase.number} · {_e(phase.name)}</b>")
            if phase.focus.strip():
                lines.append(f"<i>{_e(phase.focus.strip())}</i>")
            km = macro.weekly_km[min(week_number, len(macro.weekly_km)) - 1]
            lines.append(f"Плановый объём недели: {_km(km)} км")
    if macro is None:
        lines.append("ℹ️ План в старом формате: фазы не показать, пересоздайте его через /plan.")

    lines.append("")
    if goal_line:
        lines.append(goal_line)
    else:
        lines.append("⏱ Цель по времени не задана: план строится по текущей форме.")
        if forecast_line:
            lines.append(forecast_line)
    return "\n".join(lines)


def render_vdot_review(
    review: VdotReview,
    zones: Optional[TrainingZones] = None,
    goal_note: Optional[str] = None,
) -> str:
    """Итог пересмотра VDOT раз в 4 недели. zones: зоны по новому VDOT; goal_note: готовый HTML о цели."""
    if review.change == VdotChange.RAISED:
        head = f"📈 Форма выросла: VDOT <b>{review.old:g} → {review.new:g}</b>"
    elif review.change == VdotChange.LOWERED:
        head = f"📉 Форма снизилась: VDOT <b>{review.old:g} → {review.new:g}</b>"
    else:
        head = f"VDOT без изменений: <b>{review.old:g}</b>"
    lines = ["🔬 <b>Пересмотр формы</b> (раз в 4 недели)", head, f"<i>{_e(review.reason)}</i>"]
    if review.change != VdotChange.KEPT and zones is not None:
        lines += ["", "Темпы тренировок обновлены:", render_zones(zones)]
    if goal_note:
        lines += ["", goal_note]
    return "\n".join(lines)


def render_replan(replan: Replan) -> str:
    """Пересчёт оставшихся недель: причины и километраж «было → стало» с недели пересчёта."""
    start = replan.from_week
    old = SEP.join(_km(v) for v in replan.old_km[start - 1:])
    new = SEP.join(_km(v) for v in replan.macro.weekly_km[start - 1:])
    end = len(replan.old_km)
    weeks = f"нед. {start}" if start == end else f"нед. {start}–{end}"
    lines = [
        "🔄 <b>План пересчитан</b>",
        "Причина: " + _e("; ".join(replan.reasons)),
        "",
        f"Километраж ({weeks}):",
        f"было: {old}",
        f"стало: <b>{new}</b>",
        "",
        "<i>Объём восстанавливается постепенно, не больше +10% в неделю. Дата забега и фазы не меняются.</i>",
    ]
    return "\n".join(lines)

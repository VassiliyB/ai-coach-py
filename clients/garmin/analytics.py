from typing import Any, Dict, List, Optional

from services.coach_service import MAX_VDOT, MIN_VDOT, calculate_vdot


def format_pace(pace_sec_per_km: float) -> str:
    """Форматирует темп в секундах на км в строку 'М:СС /км'."""
    if pace_sec_per_km <= 0 or pace_sec_per_km > 3600:
        return "--:-- /км"
    minutes = int(pace_sec_per_km // 60)
    seconds = int(pace_sec_per_km % 60)
    return f"{minutes}:{seconds:02d} /км"


def parse_last_activity(raw_activity: Dict[str, Any]) -> Dict[str, Any]:
    """Преобразует сырой JSON активности Garmin в плоскую структуру."""
    act_type = raw_activity.get("activityType", {}).get("typeKey", "unknown")
    dist_m = raw_activity.get("distance", 0.0)
    dur_s = raw_activity.get("duration", 0.0)
    pace_sec = (dur_s / (dist_m / 1000.0)) if dist_m > 0 else 0.0

    return {
        "activity_id": str(raw_activity.get("activityId")),
        "name": raw_activity.get("activityName"),
        "start_time": raw_activity.get("startTimeLocal"),
        "start_time_gmt": raw_activity.get("startTimeGMT"),  # для поллинга: возраст тренировки в UTC
        "activity_type": act_type,
        "is_running": "running" in act_type.lower(),
        "distance_km": round(dist_m / 1000.0, 2),
        "duration_minutes": round(dur_s / 60.0, 1),
        "avg_pace_formatted": format_pace(pace_sec),
        "avg_pace_sec": round(pace_sec, 1) if pace_sec > 0 else None,  # для расчёта зоны кодом
        "avg_heart_rate": raw_activity.get("averageHR"),
        "max_heart_rate": raw_activity.get("maxHR"),
        "aerobic_te": raw_activity.get("aerobicTrainingEffect"),
        "anaerobic_te": raw_activity.get("anaerobicTrainingEffect"),
        "elevation_gain_m": raw_activity.get("elevationGain"),
        "raw_data": raw_activity,
    }


def find_best_effort(activities: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Лучшая пробежка (от 3 км) по расчётному VDOT.

    Возвращает {"distance_m", "time_s", "vdot"} или None, если подходящих пробежек нет.
    """
    best: Optional[Dict[str, Any]] = None
    for act in activities:
        dist = act.get("distance") or 0.0
        dur = act.get("duration") or 0.0
        if dist < 3000 or dur <= 0:
            continue
        vdot = calculate_vdot(dist, dur)
        if not (MIN_VDOT <= vdot <= MAX_VDOT):
            continue  # отсеиваем артефакты GPS и паузы
        if best is None or vdot > best["vdot"]:
            best = {"distance_m": dist, "time_s": dur, "vdot": vdot}
    return best


def aggregate_profile_90d(
    activities: List[Dict[str, Any]],
    vo2_max: Optional[float],
    days: int = 90,
) -> Dict[str, Any]:
    """Формирует спортивный паспорт бегуна за указанный период."""
    if not activities:
        return {
            "total_runs": 0,
            "total_km": 0.0,
            "average_weekly_km": 0.0,
            "typical_easy_hr": None,
            "max_hr": None,
            "vo2_max": vo2_max,
            "best_effort": None,
            "vdot": None,
            "best_effort_distance_m": None,
            "best_effort_time_s": None,
            "summary_text": f"За последние {days} дней беговых тренировок не обнаружено.",
        }

    total_km = round(sum(a.get("distance", 0.0) for a in activities) / 1000.0, 2)
    weeks = max(1.0, days / 7.0)
    avg_weekly_km = round(total_km / weeks, 1)

    max_hrs = [a.get("maxHR") for a in activities if a.get("maxHR")]
    peak_hr = max(max_hrs) if max_hrs else None

    # Легкие пробежки: дистанция от 4 км, умеренный аэробный тренировочный эффект
    easy_hrs = [
        a.get("averageHR") for a in activities
        if a.get("averageHR")
        and a.get("distance", 0.0) >= 4000
        and (a.get("aerobicTrainingEffect") or 0) <= 3.2
    ]
    typical_easy_hr = int(sum(easy_hrs) / len(easy_hrs)) if easy_hrs else None

    best = find_best_effort(activities)
    if best:
        dist_km = best["distance_m"] / 1000.0
        time_s = best["time_s"]
        best_text = (
            f"{dist_km:.1f} км за {int(time_s // 60)}:{int(time_s % 60):02d} "
            f"(темп {format_pace(time_s / dist_km)}), VDOT ≈ {best['vdot']:.1f}"
        )
    else:
        best_text = "Недостаточно данных (нужна пробежка от 3 км)"

    summary_text = (
        f"Спортивный паспорт за {days} дней:\n"
        f"- Выполнено пробежек: {len(activities)}\n"
        f"- Общий километраж: {total_km} км\n"
        f"- Средненедельный объем: {avg_weekly_km} км/нед\n"
        f"- VO2 Max: {vo2_max or 'н/д'}\n"
        f"- Пиковый ЧСС: {peak_hr or 'н/д'} уд/мин\n"
        f"- Базовый пульс легкого бега: ~{typical_easy_hr or 'н/д'} уд/мин\n"
        f"- Контрольный забег: {best_text}"
    )

    return {
        "total_runs": len(activities),
        "total_km": total_km,
        "average_weekly_km": avg_weekly_km,
        "typical_easy_hr": typical_easy_hr,
        "max_hr": peak_hr,
        "vo2_max": vo2_max,
        "best_effort": best_text,
        "vdot": round(best["vdot"], 1) if best else None,
        "best_effort_distance_m": best["distance_m"] if best else None,
        "best_effort_time_s": best["time_s"] if best else None,
        "summary_text": summary_text,
    }
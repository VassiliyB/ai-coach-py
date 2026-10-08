# try_plan_generator.py: ручная проверка генерации с настоящей моделью (нужен GROQ_API_KEY в .env)
import asyncio

from services.coach_service import calculate_zones
from services.plan_generator import PlanGenerator
from services.plan_paces import pace_text

PROFILE = {
    "summary_text": (
        "Спортивный паспорт за 90 дней:\n"
        "- Выполнено пробежек: 24\n"
        "- Общий километраж: 180 км\n"
        "- Средненедельный объем: 20 км/нед\n"
        "- VO2 Max: 48\n"
        "- Пиковый ЧСС: 188 уд/мин\n"
        "- Базовый пульс легкого бега: ~142 уд/мин\n"
        "- Контрольный забег: 5.0 км за 24:30 (темп 4:54 /км), VDOT ≈ 41.0"
    )
}
RACE = "21.1 км (Полумарафон)"
VDOT = 41.0


async def main() -> None:
    gen = PlanGenerator()

    print("Генерация макроплана...")
    macro = await gen.generate_macro(PROFILE, RACE, "2027-06-15", 12)
    for p in macro.phases:
        print(f"  Фаза {p.number}: {p.name}, недель: {p.weeks}")
    print("  Километраж по неделям:", macro.weekly_km)

    print("\nГенерация недели №1...")
    week = await gen.generate_week(PROFILE, RACE, macro, 1, "01.03.2027", "07.03.2027")
    zones = calculate_zones(VDOT)
    for d in week.days:
        print(f"  {d.day} | {d.type.value:<10} | {d.distance_km or '-':>5} км | "
              f"зона {d.zone or '-'} | {pace_text(d, zones) or '-':<16} | {d.description}")
    print(f"\nИтого за неделю: {week.total_km} км")


if __name__ == "__main__":
    asyncio.run(main())
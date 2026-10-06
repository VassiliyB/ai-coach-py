# test_ai.py
import asyncio
from services.ai_coach_service import AICoachService


async def main():
    coach = AICoachService()

    mock_profile = {
        "summary_text": (
            "Спортивный паспорт за 90 дней:\n"
            "- Выполнено пробежек: 24\n"
            "- Общий километраж: 180 км\n"
            "- Средненедельный объем: 20 км/нед\n"
            "- VO2 Max: 48\n"
            "- Пиковый ЧСС: 188 уд/мин\n"
            "- Базовый пульс легкого бега: ~142 уд/мин\n"
            "- Контрольный забег: 5.0 км за 24:30 (темп 4:54 /км)"
        )
    }

    print("Генерация макроплана через Groq AI...")
    plan = await coach.generate_macrocycle_plan(
        athlete_profile=mock_profile,
        target_race="21.1 км (Полумарафон)",
        race_date="2026-06-15",
        total_weeks=12,
    )

    print("\n--- СГЕНЕРИРОВАННЫЙ ПЛАН (САНИТИЗИРОВАННЫЙ HTML) ---\n")
    print(plan[:800] + "\n...[сокращено для теста]")


if __name__ == "__main__":
    asyncio.run(main())
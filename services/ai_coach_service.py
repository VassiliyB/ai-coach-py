# services/ai_coach_service.py
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from clients.ai_client import AIClient
from services.message_service import MessageService

logger = logging.getLogger(__name__)

KNOWLEDGE_FILE_PATH = Path("sports_knowledge.txt")


class AICoachService:
    """Сервис персонального ИИ-тренера по бегу."""

    def __init__(
        self,
        ai_client: Optional[AIClient] = None,
        knowledge_path: Path = KNOWLEDGE_FILE_PATH,
    ) -> None:
        self.ai_client = ai_client or AIClient()
        self.knowledge_base = self._load_knowledge_base(knowledge_path)

    @staticmethod
    def _load_knowledge_base(path: Path) -> str:
        """Загружает базу знаний по физиологии и правилам тренировок."""
        if path.exists():
            try:
                content = path.read_text(encoding="utf-8")
                logger.info("База знаний спортивной физиологии успешно загружена (%d симв.)", len(content))
                return content
            except Exception as e:
                logger.error("Не удалось прочитать базу знаний %s: %s", path, e)
        else:
            logger.warning("Файл %s не найден! ИИ будет работать на базовых инструкциях.", path)
        return ""

    def _build_system_prompt(self) -> str:
        """Формирует системную роль тренера с внедренной базой знаний."""
        return (
            "Ты — Garmin AI Coach, профессиональный и чуткий персональный тренер по бегу.\n"
            "Твоя методология строго базируется на двух источниках:\n"
            "1. 'Формула бега' Джека Дэниелса (шкала VDOT, зоны E, M, T, I, R, 4-фазная периодизация).\n"
            "2. 'Бег по правилу 80/20' Мэта Фицджеральда (80% в зонах 1-2, 20% развивающего бега, избегать 'серой зоны').\n\n"
            f"--- ЭТАЛОННАЯ БАЗА ЗНАНИЙ ---\n{self.knowledge_base}\n-----------------------------\n\n"
            "ТРЕБОВАНИЯ К ОТВЕТАМ:\n"
            "- Отвечай на русском языке, доброжелательно, аргументированно и по делу.\n"
            "- Используй Telegram HTML разметку (<b>жирный</b>, <i>курсив</i>, <code>код/темп</code>).\n"
            "- НЕ используй неподдерживаемые теги (<p>, <div>, <h1>, <br>).\n"
            "- Указывай конкретный целевой темп (мин:сек /км) и границы пульса для тренировок.\n"
        )

    async def generate_macrocycle_plan(
        self,
        athlete_profile: Dict[str, Any],
        target_race: str,
        race_date: str,
        total_weeks: int,
    ) -> str:
        """Генерирует стратегический макроцикл подготовки по 4 фазам Дэниелса."""
        system_prompt = self._build_system_prompt()

        user_prompt = (
            f"Сформируй макроцикл подготовки к забегу: <b>{target_race}</b>.\n"
            f"Дата старта: <b>{race_date}</b> (всего недель на подготовку: {total_weeks}).\n\n"
            f"ПАСПОРТ АТЛЕТА (за последние 90 дней):\n"
            f"{athlete_profile.get('summary_text', 'Нет данных')}\n\n"
            "ЗАДАЧА:\n"
            "1. Оцени текущий VDOT атлета по его контрольному забегу/пробежкам и рассчитай персональные зоны темпа (E, M, T, I, R).\n"
            f"2. Разбей период ({total_weeks} нед.) на 4 фазы Дэниелса:\n"
            "   - Фаза I: Закладка фундамента (аэробная база).\n"
            "   - Фаза II: Раннее качество (R-повторы, техника, экономичность).\n"
            "   - Фаза III: Переходное качество (T-порог и I-интервалы).\n"
            "   - Фаза IV: Финальная подводка (тейпер, выход на пик).\n"
            "3. Укажи ориентировочный пиковый недельный километраж и правила предосторожности."
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        raw_response = await self.ai_client.generate_response(messages, temperature=0.3)
        return MessageService.sanitize_telegram_html(raw_response)

    async def generate_weekly_microcycle(
        self,
        athlete_profile: Dict[str, Any],
        target_race: str,
        macro_plan_summary: str,
        week_number: int,
        week_start: str,
        week_end: str,
    ) -> str:
        """Генерирует недельный микроцикл (Пн–Вс) по правилу 80/20."""
        system_prompt = self._build_system_prompt()

        user_prompt = (
            f"Составь подробное недельное расписание тренировок (Неделя №{week_number}).\n"
            f"Период: с {week_start} по {week_end}.\n"
            f"Целевой старт: {target_race}.\n\n"
            f"КОНТЕКСТ МАКРОПЛАНА:\n{macro_plan_summary[:1500]}\n\n"
            f"ТЕКУЩАЯ ФОРМА АТЛЕТА:\n{athlete_profile.get('summary_text', 'Нет данных')}\n\n"
            "ТРЕБОВАНИЯ К НЕДЕЛЕ:\n"
            "- Распиши каждый день: Пн, Вт, Ср, Чт, Пт, Сб, Вс.\n"
            "- Включи 1-2 дня полного отдыха или ОФП/растяжки.\n"
            "- Соблюдай соотношение 80% легкого объема к 20% скоростного.\n"
            "- Для каждой пробежки укажи: Дистанцию (км), Зону Дэниелса, целевой Темп (М:СС /км) и целевой Пульс."
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        raw_response = await self.ai_client.generate_response(messages, temperature=0.4)
        return MessageService.sanitize_telegram_html(raw_response)

    async def analyze_activity(
        self,
        athlete_profile: Dict[str, Any],
        activity: Dict[str, Any],
    ) -> str:
        """Экспресс-анализ завершенной пробежки («План vs Факт»)."""
        system_prompt = self._build_system_prompt()

        user_prompt = (
            "Проведи быстрый разбор завершенной пробежки атлета:\n"
            f"- Название: {activity.get('name')}\n"
            f"- Дистанция: {activity.get('distance_km')} км\n"
            f"- Время: {activity.get('duration_minutes')} мин\n"
            f"- Средний темп: {activity.get('avg_pace_formatted')}\n"
            f"- Средний / Макс ЧСС: {activity.get('avg_heart_rate')} / {activity.get('max_heart_rate')} уд/мин\n"
            f"- Training Effect: Аэробный {activity.get('aerobic_te')}, Анаэробный {activity.get('anaerobic_te')}\n\n"
            f"Профиль атлета: {athlete_profile.get('summary_text', 'Нет данных')}\n\n"
            "Дай краткий вердикт:\n"
            "1. В какую зону попала тренировка? Не было ли заваливания в 'серую зону'?\n"
            "2. Оценка физиологической нагрузки (Training Effect).\n"
            "3. Четкая рекомендация на завтра (отдых, легкая пробежка или день ОФП)."
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        raw_response = await self.ai_client.generate_response(messages, temperature=0.3)
        return MessageService.sanitize_telegram_html(raw_response)
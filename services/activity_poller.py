# services/activity_poller.py
"""Опрос Garmin: новые пробежки пользователей автоматически получают разбор в Telegram."""
import html
import logging
from datetime import datetime, timezone
from typing import Optional

from aiogram import Bot

from clients.garmin import GarminAuthError, GarminClient, GarminRateLimitError
from database import async_session_maker
from models.athlete_profile import AthleteProfile
from models.user import AppUser
from services.activity_polling import FETCH_LIMIT, activity_id, select_for_analysis
from services.ai_coach_service import AICoachService
from services.coach_service import build_profile_context, zones_for_profile
from services.garmin_link import RELOGIN_TEXT, reset_garmin_link
from services.message_service import MessageService
from services.user_locks import UserLocks
from services.user_service import UserService

logger = logging.getLogger(__name__)


class ActivityPoller:
    def __init__(
        self, bot: Bot, garmin: GarminClient, ai_coach: AICoachService, locks: Optional[UserLocks] = None,
    ) -> None:
        self.bot = bot
        self.garmin = garmin
        self.ai_coach = ai_coach
        self.locks = locks or UserLocks()

    async def poll_all(self) -> None:
        """Один круг опроса всех пользователей с привязанным Garmin (последовательно)."""
        async with async_session_maker() as session:
            users = await UserService.list_linked_users_with_profiles(session)

        analyzed = 0
        for user, profile in users:
            if not self.garmin.has_saved_tokens(user.telegram_chat_id):
                continue
            try:
                async with self.locks.hold(user.telegram_chat_id, "опрос Garmin") as acquired:
                    if not acquired:  # у пользователя идёт команда: опросим в следующий раз
                        logger.info(
                            "Опрос пропущен для user_id=%s: идёт %s",
                            user.id, self.locks.current(user.telegram_chat_id),
                        )
                        continue
                    analyzed += await self.poll_user(user, profile)
            except GarminRateLimitError as exc:
                # 429 блокирует IP целиком: остальных пользователей опросим в следующий раз
                logger.warning("Опрос Garmin прерван на user_id=%s: %s", user.id, exc)
                break
            except GarminAuthError:
                # Сброс привязки исключает пользователя из опроса: уведомление уходит один раз
                try:
                    await reset_garmin_link(self.garmin, user.telegram_chat_id)
                    await self.bot.send_message(
                        user.telegram_chat_id,
                        f"{RELOGIN_TEXT}\nДо этого автоматический разбор пробежек приостановлен.",
                        parse_mode="HTML",
                    )
                except Exception as exc:  # например, пользователь заблокировал бота: опрос остальных идёт дальше
                    logger.warning("Не удалось уведомить об истёкшей сессии user_id=%s: %s", user.id, exc)
            except Exception as exc:  # сбой одного пользователя не останавливает опрос остальных
                logger.exception("Ошибка опроса Garmin для user_id=%s: %s", user.id, exc)
        logger.info("Опрос Garmin завершён: пользователей %d, разобрано тренировок %d", len(users), analyzed)

    async def poll_user(
        self, user: AppUser, profile: Optional[AthleteProfile], now: Optional[datetime] = None,
    ) -> int:
        """Разбирает новые пробежки пользователя. Возвращает число отправленных разборов."""
        activities = await self.garmin.get_recent_activities(user.telegram_chat_id, limit=FETCH_LIMIT)
        with_ids = [a for a in activities if activity_id(a)]

        async with async_session_maker() as session:
            # Первый опрос: всё, что уже есть в Garmin, помечается без разбора
            if not await UserService.has_processed_activities(session, user.id):
                await UserService.mark_activities_processed(session, user.id, [activity_id(a) for a in with_ids])
                logger.info("Первый опрос user_id=%s: помечено тренировок %d", user.id, len(with_ids))
                return 0
            # Захват до разбора: повторный или параллельный опрос не отправит разбор дважды
            new = [a for a in with_ids if await UserService.claim_activity(session, user.id, activity_id(a))]

        to_analyze = select_for_analysis(new, now or datetime.now(timezone.utc))
        if new:
            logger.info(
                "user_id=%s: новых тренировок %d, к разбору %d", user.id, len(new), len(to_analyze),
            )
        for act in to_analyze:
            await self._send_analysis(user, profile, act)
        return len(to_analyze)

    async def _send_analysis(self, user: AppUser, profile: Optional[AthleteProfile], act: dict) -> None:
        analysis = await self.ai_coach.analyze_activity(
            athlete_profile=build_profile_context(profile),
            activity=act,
            zones=zones_for_profile(profile),
            max_hr=getattr(profile, "max_heart_rate", None),
        )
        name = html.escape(str(act.get("name") or "Пробежка"))
        header = f"🏃 <b>Новая тренировка</b>: {name} · {act.get('distance_km')} км\n\n"
        for chunk in MessageService.chunk_message(header + analysis):
            await self.bot.send_message(chat_id=user.telegram_chat_id, text=chunk, parse_mode="HTML")

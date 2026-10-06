import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectTooManyRequestsError,
)

from .analytics import aggregate_profile_90d, parse_last_activity
from .exceptions import GarminAuthError, GarminClientError, GarminRateLimitError
from .token_storage import GarminTokenStorage

logger = logging.getLogger(__name__)


class GarminClient:
    """Асинхронный клиент для взаимодействия с Garmin Connect."""

    def __init__(self, storage: Optional[GarminTokenStorage] = None) -> None:
        self.storage = storage or GarminTokenStorage()

    def has_saved_tokens(self, chat_id: int) -> bool:
        return self.storage.has_tokens(chat_id)

    # ---------------- Синхронные воркеры (для системных потоков) ----------------

    def _login_sync(self, chat_id: int, email: str, password: str) -> None:
        try:
            client = Garmin(email=email, password=password)
            client.login()
            client.garth.dump(str(self.storage.get_user_dir(chat_id)))
            logger.info("Сессия Garmin успешно сохранена для chat_id=%s", chat_id)
        except GarminConnectTooManyRequestsError as exc:
            raise GarminRateLimitError("Garmin SSO временно заблокировал доступ (429).") from exc
        except (GarminConnectAuthenticationError, Exception) as exc:
            raise GarminAuthError("Неверный email или пароль Garmin.") from exc

    def _init_session_sync(self, chat_id: int) -> Garmin:
        if not self.storage.has_tokens(chat_id):
            raise GarminAuthError("Сессия не найдена. Требуется авторизация.")
        try:
            client = Garmin()
            client.login(tokenstore=str(self.storage.get_user_dir(chat_id)))
            return client
        except GarminConnectTooManyRequestsError as exc:
            raise GarminRateLimitError("Превышен лимит запросов к Garmin API (429).") from exc
        except Exception as exc:
            raise GarminAuthError("Сессия истекла. Требуется повторный вход.") from exc

    def _fetch_last_activity_sync(self, chat_id: int) -> Optional[Dict[str, Any]]:
        client = self._init_session_sync(chat_id)
        activities = client.get_activities(start=0, limit=1)
        return parse_last_activity(activities[0]) if activities else None

    def _fetch_profile_90d_sync(self, chat_id: int, days: int) -> Dict[str, Any]:
        client = self._init_session_sync(chat_id)

        # 1. VO2 Max
        vo2_max = None
        try:
            metrics = client.get_max_metrics(date.today().isoformat())
            if metrics:
                generic = metrics[0].get("generic", {})
                vo2_max = generic.get("vo2MaxPreciseValue") or generic.get("vo2MaxValue")
        except Exception as e:
            logger.warning("Не удалось получить VO2 Max для chat_id=%s: %s", chat_id, e)

        # 2. Выгрузка беговых тренировок за указанный интервал
        runs: List[Dict[str, Any]] = []
        cutoff = datetime.now() - timedelta(days=days)
        start, limit = 0, 50

        while True:
            batch = client.get_activities(start=start, limit=limit)
            if not batch:
                break

            finished = False
            for act in batch:
                ts = act.get("startTimeLocal")
                if ts and datetime.fromisoformat(ts.replace("Z", "")) < cutoff:
                    finished = True
                    break
                if "running" in act.get("activityType", {}).get("typeKey", "").lower():
                    runs.append(act)

            if finished or len(batch) < limit:
                break
            start += limit

        return aggregate_profile_90d(runs, vo2_max, days=days)

    # ---------------- Публичный асинхронный интерфейс ----------------

    async def login_with_credentials(self, chat_id: int, email: str, password: str) -> None:
        await asyncio.to_thread(self._login_sync, chat_id, email, password)

    async def get_last_activity(self, chat_id: int) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self._fetch_last_activity_sync, chat_id)

    async def get_athlete_profile_90d(self, chat_id: int, days: int = 90) -> Dict[str, Any]:
        return await asyncio.to_thread(self._fetch_profile_90d_sync, chat_id, days)

    async def clear_session(self, chat_id: int) -> None:
        await asyncio.to_thread(self.storage.clear, chat_id)
import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

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
    """Асинхронный клиент для взаимодействия с Garmin Connect с поддержкой MFA."""

    def __init__(self, storage: Optional[GarminTokenStorage] = None) -> None:
        self.storage = storage or GarminTokenStorage()
        # Хранилище временных клиентов в процессе прохождения MFA: {chat_id: (Garmin, client_state)}
        self._pending_auth: Dict[int, Tuple[Garmin, Any]] = {}

    def has_saved_tokens(self, chat_id: int) -> bool:
        return self.storage.has_tokens(chat_id)

    # ---------------- Вспомогательное сохранение токенов ----------------

    def _save_tokens_to_storage(self, client: Garmin, chat_id: int) -> None:
        """Сохраняет токены сессии на диск для garminconnect >= 0.3.x."""
        user_dir = str(self.storage.get_user_dir(chat_id))

        # 1. Для garminconnect >= 0.3.x метод dump живет в client.client
        if hasattr(client, "client") and hasattr(client.client, "dump"):
            client.client.dump(user_dir)
            logger.info("Токены успешно сохранены через client.client.dump в %s", user_dir)
        # 2. Запасные варианты для разных версий
        elif hasattr(client, "dump"):
            client.dump(user_dir)
            logger.info("Токены успешно сохранены через client.dump в %s", user_dir)
        elif hasattr(client, "garth") and hasattr(client.garth, "dump"):
            client.garth.dump(user_dir)

    # ---------------- Синхронные воркеры входа ----------------

    def _login_step1_sync(self, chat_id: int, email: str, password: str) -> Tuple[str, Optional[Garmin]]:
        """
        Первый шаг: отправка логина и пароля.
        Возвращает:
          - ("success", client) — если вошли сразу без 2FA
          - ("needs_mfa", client) — если Garmin выслал код на почту
        """
        try:
            user_dir = str(self.storage.get_user_dir(chat_id))
            client = Garmin(email=email, password=password, return_on_mfa=True)
            # Передаем tokenstore напрямую — библиотека сохранит токены автоматически
            status, client_state = client.login(tokenstore=user_dir)

            if status == "needs_mfa":
                self._pending_auth[chat_id] = (client, client_state)
                return "needs_mfa", client

            # Дополнительно фиксируем через наш метод
            self._save_tokens_to_storage(client, chat_id)
            return "success", client

        except GarminConnectTooManyRequestsError as exc:
            raise GarminRateLimitError("Garmin SSO временно заблокировал доступ (429). Подождите 15 минут.") from exc
        except GarminConnectAuthenticationError as exc:
            raise GarminAuthError("Неверный email или пароль Garmin.") from exc
        except Exception as exc:
            err = str(exc)
            if "429" in err:
                raise GarminRateLimitError("Превышен лимит запросов к Garmin (429).") from exc
            raise GarminClientError(f"Сбой подключения к Garmin: {err}") from exc

    def _login_step2_mfa_sync(self, chat_id: int, mfa_code: str) -> None:
        """Второй шаг: подтверждение 6-значного кода MFA."""
        if chat_id not in self._pending_auth:
            raise GarminAuthError("Сессия ввода кода устарела. Начните вход заново.")

        client, client_state = self._pending_auth[chat_id]
        try:
            client.resume_login(client_state, mfa_code)
            self._save_tokens_to_storage(client, chat_id)
            self._pending_auth.pop(chat_id, None)
            logger.info("MFA успешно завершен, токены сохранены для chat_id=%s", chat_id)
        except Exception as exc:
            self._pending_auth.pop(chat_id, None)
            raise GarminAuthError(f"Неверный код подтверждения: {exc}") from exc

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

    def _fetch_recent_activities_sync(self, chat_id: int, limit: int) -> List[Dict[str, Any]]:
        client = self._init_session_sync(chat_id)
        try:
            activities = client.get_activities(start=0, limit=limit)
        except GarminConnectTooManyRequestsError as exc:
            raise GarminRateLimitError("Превышен лимит запросов к Garmin API (429).") from exc
        return [parse_last_activity(act) for act in activities or []]

    def _fetch_profile_90d_sync(self, chat_id: int, days: int) -> Dict[str, Any]:
        client = self._init_session_sync(chat_id)

        vo2_max = None
        try:
            metrics = client.get_max_metrics(date.today().isoformat())
            if metrics:
                generic = metrics[0].get("generic", {})
                vo2_max = generic.get("vo2MaxPreciseValue") or generic.get("vo2MaxValue")
        except Exception as e:
            logger.warning("Не удалось получить VO2 Max для chat_id=%s: %s", chat_id, e)

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

    async def login_start(self, chat_id: int, email: str, password: str) -> str:
        """Запуск авторизации. Возвращает 'success' или 'needs_mfa'."""
        status, _ = await asyncio.to_thread(self._login_step1_sync, chat_id, email, password)
        return status

    async def login_complete_mfa(self, chat_id: int, mfa_code: str) -> None:
        """Завершение входа по коду из письма."""
        await asyncio.to_thread(self._login_step2_mfa_sync, chat_id, mfa_code)

    async def get_last_activity(self, chat_id: int) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self._fetch_last_activity_sync, chat_id)

    async def get_recent_activities(self, chat_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        """Последние тренировки (новые первыми) в плоском формате parse_last_activity."""
        return await asyncio.to_thread(self._fetch_recent_activities_sync, chat_id, limit)

    async def get_athlete_profile_90d(self, chat_id: int, days: int = 90) -> Dict[str, Any]:
        return await asyncio.to_thread(self._fetch_profile_90d_sync, chat_id, days)

    async def clear_session(self, chat_id: int) -> None:
        await asyncio.to_thread(self.storage.clear, chat_id)

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from .analytics import aggregate_profile_90d, parse_hrv_status, parse_last_activity, parse_training_readiness
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
        except GarminConnectAuthenticationError as exc:
            # Только здесь токены действительно непригодны: вызывающий код сбрасывает привязку
            raise GarminAuthError("Сессия истекла. Требуется повторный вход.") from exc
        except GarminConnectConnectionError as exc:
            raise GarminClientError(f"Нет связи с Garmin: {exc}") from exc
        except Exception as exc:
            # Непонятная ошибка не повод удалять токены: они могут быть рабочими
            raise GarminClientError(f"Сбой при подключении к Garmin: {exc}") from exc

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

    def _api_call(self, func: Any, *args: Any) -> Any:
        """Вызов API Garmin с переводом ошибок библиотеки в наши исключения."""
        try:
            return func(*args)
        except GarminConnectTooManyRequestsError as exc:
            raise GarminRateLimitError("Превышен лимит запросов к Garmin API (429).") from exc
        except GarminConnectAuthenticationError as exc:
            raise GarminAuthError("Сессия истекла. Требуется повторный вход.") from exc
        except Exception as exc:
            raise GarminClientError(f"Сбой запроса к Garmin: {exc}") from exc

    def _schedule_workouts_sync(
        self, chat_id: int, workouts: List[Tuple[str, Dict[str, Any]]], replace_ids: List[int],
    ) -> List[Dict[str, Any]]:
        client = self._init_session_sync(chat_id)

        # Старые тренировки этой недели: удаление шаблона убирает его и из календаря
        for workout_id in replace_ids:
            try:
                self._api_call(client.delete_workout, workout_id)
            except (GarminRateLimitError, GarminAuthError):
                raise
            except GarminClientError as exc:  # пользователь мог удалить тренировку сам
                logger.warning("Не удалось удалить тренировку %s (chat_id=%s): %s", workout_id, chat_id, exc)

        created: List[Dict[str, Any]] = []
        try:
            for day_iso, workout in workouts:
                uploaded = self._api_call(client.upload_workout, workout)
                workout_id = int(uploaded["workoutId"])
                created.append({"date": day_iso, "workout_id": workout_id})
                self._api_call(client.schedule_workout, workout_id, day_iso)
        except Exception:
            # Неделя выгружается целиком или никак: иначе при повторе в календаре остались бы дубли
            for item in created:
                try:
                    client.delete_workout(item["workout_id"])
                except Exception as exc:
                    logger.warning("Откат: тренировка %s не удалена: %s", item["workout_id"], exc)
            raise
        return created

    def _fetch_week_facts_sync(self, chat_id: int, start: date, end: date) -> Dict[str, Any]:
        client = self._init_session_sync(chat_id)
        raw_runs = self._api_call(client.get_activities_by_date, start.isoformat(), end.isoformat(), "running")
        runs = [parse_last_activity(act) for act in raw_runs or []]

        # Показатели восстановления есть не на всех часах: их отсутствие не ошибка
        signals: Dict[str, Any] = {"hrv_status": None, "readiness": None}
        for key, func, parser in (
            ("hrv_status", client.get_hrv_data, parse_hrv_status),
            ("readiness", client.get_training_readiness, parse_training_readiness),
        ):
            try:
                signals[key] = parser(self._api_call(func, end.isoformat()))
            except (GarminRateLimitError, GarminAuthError):
                raise
            except GarminClientError as exc:
                logger.info("Нет данных %s для chat_id=%s: %s", key, chat_id, exc)
        return {"runs": runs, **signals}

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

    async def schedule_workouts(
        self, chat_id: int, workouts: List[Tuple[str, Dict[str, Any]]], replace_ids: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        """Загружает тренировки (дата YYYY-MM-DD, JSON) в библиотеку и ставит в календарь.

        replace_ids: ранее выгруженные тренировки, которые удаляются перед загрузкой.
        Возвращает [{"date", "workout_id"}]. При ошибке уже загруженные в этом вызове удаляются.
        """
        return await asyncio.to_thread(self._schedule_workouts_sync, chat_id, workouts, replace_ids or [])

    async def get_week_facts(self, chat_id: int, start: date, end: date) -> Dict[str, Any]:
        """Факт за период: беговые тренировки (формат parse_last_activity), статус HRV и готовность на дату end.

        Возвращает {"runs": [...], "hrv_status": str | None, "readiness": int | None}.
        """
        return await asyncio.to_thread(self._fetch_week_facts_sync, chat_id, start, end)

    async def clear_session(self, chat_id: int) -> None:
        """Забывает пользователя: незавершённый вход по MFA в памяти и файл токенов на диске."""
        self._pending_auth.pop(chat_id, None)
        await asyncio.to_thread(self.storage.clear, chat_id)

import shutil
from pathlib import Path
from typing import Optional

from config import settings


class GarminTokenStorage:
    """Управление файлами токенов пользователей на диске."""

    TOKEN_FILENAME = "garmin_tokens.json"

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        self.base_dir = base_dir or settings.GARMIN_TOKENS_DIR
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _user_dir(self, chat_id: int) -> Path:
        """Путь к папке пользователя без создания."""
        return self.base_dir / str(chat_id)

    def get_user_dir(self, chat_id: int) -> Path:
        """Путь к папке пользователя (создаётся, если её нет)."""
        user_dir = self._user_dir(chat_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        return user_dir

    def get_token_file(self, chat_id: int) -> Path:
        """Путь к файлу токенов (без побочных эффектов)."""
        return self._user_dir(chat_id) / self.TOKEN_FILENAME

    def has_tokens(self, chat_id: int) -> bool:
        token_file = self.get_token_file(chat_id)
        return token_file.is_file() and token_file.stat().st_size > 0

    def clear(self, chat_id: int) -> None:
        user_dir = self._user_dir(chat_id)
        if user_dir.exists():
            shutil.rmtree(user_dir, ignore_errors=True)
import shutil
from pathlib import Path

DEFAULT_TOKENS_DIR = Path(".garmin_tokens")


class GarminTokenStorage:
    """Управление файлами токенов пользователей на диске."""

    def __init__(self, base_dir: Path = DEFAULT_TOKENS_DIR) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def get_user_dir(self, chat_id: int) -> Path:
        user_dir = self.base_dir / str(chat_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        return user_dir

    def get_token_file(self, chat_id: int) -> Path:
        return self.get_user_dir(chat_id) / "garmin_tokens.json"

    def has_tokens(self, chat_id: int) -> bool:
        token_file = self.get_token_file(chat_id)
        return token_file.exists() and token_file.stat().st_size > 0

    def clear(self, chat_id: int) -> None:
        user_dir = self.base_dir / str(chat_id)
        if user_dir.exists():
            shutil.rmtree(user_dir)
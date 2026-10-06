# services/message_service.py
import re
from typing import List

TELEGRAM_MAX_LENGTH = 4000

# Регулярное выражение для поиска легитимных тегов Telegram
VALID_TELEGRAM_TAG_PATTERN = re.compile(
    r"</?(?:b|strong|i|em|u|ins|s|strike|del|code|pre|blockquote)>|<a\s+href=[\"'][^\"']+[\"']>",
    re.IGNORECASE,
)


class MessageService:
    """Сервис для очистки, форматирования и безопасной нарезки сообщений для Telegram."""

    @staticmethod
    def sanitize_telegram_html(text: str) -> str:
        """Очищает и нормализует HTML для Telegram Bot API."""
        if not text:
            return ""

        # 1. Заменяем markdown жирный шрифт (**текст**) на <b>текст</b>
        text = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", text)

        # 2. Удаляем неподдерживаемые блочные HTML-теги, заменяя их на перенос строки
        unsupported_tags = [
            r"</?p>", r"</?div>", r"</?h[1-6]>",
            r"</?ul>", r"</?ol>", r"</?li>", r"<br\s*/?>"
        ]
        for pattern in unsupported_tags:
            text = re.sub(pattern, "\n", text, flags=re.IGNORECASE)

        # 3. Безопасное экранирование сырых знаков < и > через плейсхолдеры
        placeholders: List[str] = []

        def _save_tag(match: re.Match) -> str:
            placeholders.append(match.group(0))
            return f"___TG_TAG_{len(placeholders) - 1}___"

        # Прячем легитимные теги
        text = VALID_TELEGRAM_TAG_PATTERN.sub(_save_tag, text)

        # Теперь все оставшиеся < и > — сырые математические знаки
        text = text.replace("<", "&lt;").replace(">", "&gt;")

        # Возвращаем легитимные теги обратно
        for idx, tag in enumerate(placeholders):
            text = text.replace(f"___TG_TAG_{idx}___", tag)

        # 4. Балансировка незакрытых тегов (если LLM оборвала ответ на середине)
        for tag in ["b", "i", "code", "pre", "blockquote"]:
            open_count = len(re.findall(rf"<{tag}\b[^>]*>", text, re.IGNORECASE))
            close_count = len(re.findall(rf"</{tag}>", text, re.IGNORECASE))
            if open_count > close_count:
                text += f"</{tag}>" * (open_count - close_count)

        # 5. Убираем избыточные пустые строки
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def chunk_message(text: str, max_length: int = TELEGRAM_MAX_LENGTH) -> List[str]:
        """Разбивает длинный текст на части по границе строк, не разрывая форматирование."""
        if len(text) <= max_length:
            return [text]

        chunks: List[str] = []
        lines = text.split("\n")
        current_chunk = ""

        for line in lines:
            if len(current_chunk) + len(line) + 1 > max_length:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                    current_chunk = ""
                if len(line) > max_length:
                    for i in range(0, len(line), max_length):
                        chunks.append(line[i:i + max_length])
                else:
                    current_chunk = line + "\n"
            else:
                current_chunk += line + "\n"

        if current_chunk.strip():
            chunks.append(current_chunk.strip())

        return chunks
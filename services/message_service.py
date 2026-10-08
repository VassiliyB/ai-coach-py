# services/message_service.py
import re
from typing import List, Tuple

TELEGRAM_MAX_LENGTH = 4000   # лимит Telegram 4096, оставляем запас
_CHUNK_RESERVE = 200         # запас под закрывающие и повторно открываемые теги

_FORMAT_TAGS = "b|strong|i|em|u|ins|s|strike|del|code|pre|blockquote|a"

# Любой допустимый тег Telegram (открывающий или закрывающий)
_TAG_RE = re.compile(rf"<(/?)({_FORMAT_TAGS})\b[^>]*>", re.IGNORECASE)

# Теги, которые сохраняем как есть: <a> только с href, остальные без атрибутов
_KEEP_TAG_RE = re.compile(
    r"</?(?:b|strong|i|em|u|ins|s|strike|del|code|pre|blockquote)>"
    r"|</a>"
    r"|<a\s+href=[\"'][^\"'<>]+[\"']>",
    re.IGNORECASE,
)

_ENTITY_AMP_RE = re.compile(r"&(?!(?:amp|lt|gt|quot|#\d+);)")


class MessageService:
    """Очистка, нормализация и безопасная нарезка сообщений для Telegram (parse_mode=HTML)."""

    # ---------------- Санитизация ----------------

    @staticmethod
    def sanitize_telegram_html(text: str) -> str:
        """Приводит ответ LLM к HTML, который принимает Telegram Bot API."""
        if not text:
            return ""

        # 0. Служебные теги <data>, которые модель могла повторить из промпта
        text = re.sub(r"</?data>", "", text, flags=re.IGNORECASE)

        # 1. Markdown -> HTML
        text = re.sub(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", r"<b>\1</b>", text, flags=re.MULTILINE)
        text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.DOTALL)
        text = re.sub(r"^(\s*)[*-]\s+", r"\1• ", text, flags=re.MULTILINE)
        text = re.sub(r"^\s*([-*_]\s*){3,}$", "", text, flags=re.MULTILINE)   # горизонтальные линии

        # 2. Неподдерживаемые блочные теги
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<li[^>]*>", "• ", text, flags=re.IGNORECASE)
        text = re.sub(r"</li>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<h[1-6][^>]*>", "<b>", text, flags=re.IGNORECASE)
        text = re.sub(r"</h[1-6]>", "</b>\n", text, flags=re.IGNORECASE)
        text = re.sub(r"</?(?:p|div|ul|ol|span)[^>]*>", "\n", text, flags=re.IGNORECASE)

        # 3. Прячем допустимые теги, экранируем всё остальное, возвращаем теги
        saved: List[str] = []

        def _save(match: re.Match) -> str:
            saved.append(match.group(0))
            return f"\x00{len(saved) - 1}\x00"

        text = _KEEP_TAG_RE.sub(_save, text)
        text = _ENTITY_AMP_RE.sub("&amp;", text)
        text = text.replace("<", "&lt;").replace(">", "&gt;")
        text = re.sub(r"\x00(\d+)\x00", lambda m: saved[int(m.group(1))], text)

        # 4. Правильная вложенность тегов
        text = MessageService._balance_tags(text)

        # 5. Лишние пустые строки
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def _balance_tags(text: str) -> str:
        """Удаляет «осиротевшие» закрывающие теги и закрывает незакрытые в правильном порядке."""
        out: List[str] = []
        stack: List[str] = []
        pos = 0

        for m in _TAG_RE.finditer(text):
            out.append(text[pos:m.start()])
            pos = m.end()
            closing, name = bool(m.group(1)), m.group(2).lower()

            if not closing:
                stack.append(name)
                out.append(m.group(0))
            elif name in stack:
                # закрываем вложенные теги, которые модель забыла закрыть
                while stack:
                    top = stack.pop()
                    out.append(f"</{top}>")
                    if top == name:
                        break
            # иначе закрывающий тег без пары, пропускаем

        out.append(text[pos:])
        out.extend(f"</{name}>" for name in reversed(stack))
        return "".join(out)

    # ---------------- Нарезка ----------------

    @staticmethod
    def _open_tags(chunk: str) -> List[Tuple[str, str]]:
        """Стек незакрытых тегов (имя, исходный тег) в конце фрагмента."""
        stack: List[Tuple[str, str]] = []
        for m in _TAG_RE.finditer(chunk):
            closing, name = bool(m.group(1)), m.group(2).lower()
            if not closing:
                stack.append((name, m.group(0)))
            else:
                for i in range(len(stack) - 1, -1, -1):
                    if stack[i][0] == name:
                        del stack[i:]
                        break
        return stack

    @staticmethod
    def _split_long_line(line: str, limit: int) -> List[str]:
        """Режет слишком длинную строку по пробелам, не разрывая тег."""
        parts: List[str] = []
        while len(line) > limit:
            cut = line.rfind(" ", 0, limit)
            if cut <= 0:
                cut = limit
            head = line[:cut]
            # не режем внутри тега: если после последнего '<' нет '>', переносим '<' в следующую часть
            lt, gt = head.rfind("<"), head.rfind(">")
            if lt > gt and lt > 0:
                cut = lt
                head = line[:cut]
            parts.append(head)
            line = line[cut:].lstrip(" ")
        if line:
            parts.append(line)
        return parts

    @staticmethod
    def chunk_message(text: str, max_length: int = TELEGRAM_MAX_LENGTH) -> List[str]:
        """Делит текст на части по строкам; теги закрываются в конце части и открываются заново в следующей."""
        if len(text) <= max_length:
            return [text]

        limit = max_length - _CHUNK_RESERVE
        raw: List[str] = []
        current = ""

        for line in text.split("\n"):
            pieces = MessageService._split_long_line(line, limit) if len(line) > limit else [line]
            for piece in pieces:
                if len(current) + len(piece) + 1 > limit:
                    if current.strip():
                        raw.append(current.rstrip("\n"))
                    current = ""
                current += piece + "\n"

        if current.strip():
            raw.append(current.rstrip("\n"))

        result: List[str] = []
        carry: List[Tuple[str, str]] = []
        for part in raw:
            body = "".join(tag for _, tag in carry) + part
            carry = MessageService._open_tags(body)
            body += "".join(f"</{name}>" for name, _ in reversed(carry))
            if body.strip():
                result.append(body)
        return result

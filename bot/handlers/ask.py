# bot/handlers/ask.py
"""/ask: вопрос тренеру. Ответ опирается на фрагменты книг (полнотекстовый поиск), профиль и текущий план."""
import logging
from typing import Any, Dict, List, Optional, Tuple

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.states import AskStates
from clients.ai_errors import AIClientError
from database import async_session_maker
from repositories.book_repo import BookRepository
from services.ai_coach_service import AICoachService
from services.coach_qa import (
    ASK_COMMAND,
    MAX_QUESTION_CHARS,
    SEARCH_LIMIT,
    Fragment,
    ask_limit_text,
    cited_numbers,
    expand_terms,
    format_fragments,
    plan_context,
    questions_left_note,
    render_sources,
)
from services.coach_service import build_profile_context
from services.message_service import MessageService
from services.plan_calendar import current_plan_week, monday_of
from services.plan_storage import parse_macro, parse_week
from services.user_service import UserService
from services.user_time import local_day_start, local_today

logger = logging.getLogger(__name__)
router = Router()

ASK_PROMPT = (
    "❓ Напишите вопрос тренеру одним сообщением. Например:\n"
    "• <i>Почему лёгкие пробежки нужно бегать так медленно?</i>\n"
    "• <i>Как вернуться к бегу после двух недель перерыва?</i>\n"
    "• <i>Сколько отдыхать между интервалами по 1000 м?</i>\n\n"
    "Ответ опирается на книги Дэниелса и Фицджеральда, ваш профиль и текущий план."
)


async def _load_context(chat_id: int) -> Tuple[Dict[str, Any], Optional[str]]:
    """Профиль (текст паспорта и зоны) и текущий план одним чтением; сессия закрывается до вызова модели."""
    async with async_session_maker() as session:
        user = await UserService.get_or_create_user(session, chat_id)
        profile = await UserService.get_athlete_profile(session, user.id)
        plan = await UserService.get_active_plan(session, user.id)
        today = local_today(UserService.timezone_of(user))
        weekly = None
        if plan is not None:
            weekly = await UserService.get_weekly_plan_by_start(session, plan.id, monday_of(today))

    plan_text = None
    if plan is not None:
        plan_text = plan_context(
            target_race=plan.target_race,
            race_date=plan.race_date,
            today=today,
            total_weeks=plan.total_weeks,
            week_number=current_plan_week(today, plan.race_date, plan.total_weeks),
            macro=parse_macro(plan.plan_details),
            week=parse_week(weekly.plan_details) if weekly is not None else None,
        )
    return build_profile_context(profile), plan_text


async def _find_fragments(groups: List[List[str]]) -> List[Fragment]:
    async with async_session_maker() as session:
        chunks = await BookRepository(session).search(groups, limit=SEARCH_LIMIT)
    return [Fragment(c.author, c.title, c.chapter, c.section, c.content) for c in chunks]


async def _questions_left(chat_id: int) -> Optional[int]:
    """Сколько вопросов осталось сегодня (по поясу пользователя); None, если лимита нет."""
    from config import settings
    limit = settings.ASK_DAILY_LIMIT
    if not limit:
        return None
    async with async_session_maker() as session:
        user = await UserService.get_or_create_user(session, chat_id)
        since = local_day_start(UserService.timezone_of(user))
        asked = await UserService.count_requests_since(session, chat_id, ASK_COMMAND, since)
    return max(0, limit - asked)


async def _answer(message: Message, question: str, ai_coach: AICoachService) -> None:
    question = " ".join(question.split())
    if len(question) > MAX_QUESTION_CHARS:
        await message.answer(f"Вопрос слишком длинный: сократите его до {MAX_QUESTION_CHARS} символов.")
        return
    left = await _questions_left(message.chat.id)
    if left == 0:
        await message.answer(ask_limit_text())
        return

    status_msg = await message.answer("🔎 Ищу ответ в книгах...")
    try:
        profile_ctx, plan_text = await _load_context(message.chat.id)
        terms = await ai_coach.search_terms(question)
        fragments = await _find_fragments(expand_terms(terms))
        logger.info("/ask: слова поиска %s, найдено фрагментов %d", terms, len(fragments))

        await status_msg.edit_text("🧠 Готовлю ответ...")
        answer = await ai_coach.answer_question(question, profile_ctx, plan_text, format_fragments(fragments))
        sources = render_sources(fragments, cited_numbers(answer, len(fragments)))

        await status_msg.delete()
        text = f"{answer}\n\n{sources}" if sources else answer
        note = questions_left_note(left - 1) if left is not None else None   # этот вопрос уже засчитан
        if note:
            text = f"{text}\n\n{note}"
        for chunk in MessageService.chunk_message(text):
            await message.answer(chunk, parse_mode="HTML")
    except AIClientError as exc:
        await status_msg.edit_text(f"❌ {exc}")
    except Exception as exc:
        logger.exception("Ошибка при ответе на вопрос: %s", exc)
        await status_msg.edit_text("❌ Не удалось ответить на вопрос. Попробуйте позже.")


@router.message(Command("ask"), flags={"user_lock": "/ask", "llm": "/ask"})
async def handle_ask(
    message: Message, command: CommandObject, state: FSMContext, ai_coach: AICoachService,
) -> None:
    """/ask <вопрос> отвечает сразу; /ask без текста ждёт вопрос следующим сообщением."""
    await state.clear()
    if not command.args or not command.args.strip():
        if await _questions_left(message.chat.id) == 0:   # не просить вопрос, на который не ответим
            await message.answer(ask_limit_text())
            return
        await state.set_state(AskStates.waiting_for_question)
        await message.answer(ASK_PROMPT, parse_mode="HTML")
        return
    await _answer(message, command.args, ai_coach)


@router.message(
    AskStates.waiting_for_question, F.text, ~F.text.startswith("/"), flags={"user_lock": "/ask", "llm": "/ask"},
)
async def handle_question(message: Message, state: FSMContext, ai_coach: AICoachService) -> None:
    await state.clear()
    await _answer(message, message.text, ai_coach)

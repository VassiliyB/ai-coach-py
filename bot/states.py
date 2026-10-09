# bot/states.py
from aiogram.fsm.state import State, StatesGroup


class PlanCreationStates(StatesGroup):
    """Состояния FSM для интерактивного создания целевого плана бега."""
    waiting_for_distance = State()  # Ожидание нажатия кнопки дистанции
    waiting_for_date = State()      # Ожидание ввода даты старта (ДД.ММ.ГГГГ)
    waiting_for_goal = State()      # Ожидание целевого времени или темпа (или кнопки «Без цели»)

class ShowPlanStates(StatesGroup):
    """Изменение цели активного плана из /show_plan."""
    waiting_for_new_goal = State()


class AuthStates(StatesGroup):
    """FSM-состояние ожидания 2FA/MFA кода от Garmin."""
    waiting_for_mfa = State()


class AskStates(StatesGroup):
    """/ask без текста: ждём вопрос тренеру следующим сообщением."""
    waiting_for_question = State()

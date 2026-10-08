# bot/states.py
from aiogram.fsm.state import State, StatesGroup


class PlanCreationStates(StatesGroup):
    """Состояния FSM для интерактивного создания целевого плана бега."""
    waiting_for_distance = State()  # Ожидание нажатия кнопки дистанции
    waiting_for_date = State()      # Ожидание ввода даты старта (ДД.ММ.ГГГГ)

class AuthStates(StatesGroup):
    """FSM-состояние ожидания 2FA/MFA кода от Garmin."""
    waiting_for_mfa = State()

from aiogram.fsm.state import State, StatesGroup


class NewTime(StatesGroup):
    new_time = State()

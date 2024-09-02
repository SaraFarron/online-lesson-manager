from aiogram.fsm.state import State, StatesGroup


class AddLesson(StatesGroup):
    choose_date = State()
    choose_time = State()

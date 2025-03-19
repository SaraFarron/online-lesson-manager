from aiogram.fsm.state import State, StatesGroup

class AddLessonState(StatesGroup):
    choose_day = State()

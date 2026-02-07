from aiogram.fsm.state import State, StatesGroup


class AddLesson(StatesGroup):
    scene = "add_lesson"
    command = "/" + scene
    base_callback = scene + "/"
    choose_date = State()
    choose_time = f"{base_callback}choose_time/"
    finish = f"{base_callback}finish/"

from aiogram.fsm.state import StatesGroup


class AddRecurrentLesson(StatesGroup):
    scene = "add_recurrent_lesson"
    text = "Добавить урок"
    command = "/" + scene
    base_callback = scene + "/"
    choose_weekday = f"{base_callback}choose_weekday/"
    choose_time = f"{base_callback}choose_time/"

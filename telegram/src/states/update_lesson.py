from aiogram.fsm.state import State, StatesGroup


class UpdateLesson(StatesGroup):
    scene = "move_lesson"
    text = "Отменить/перенести урок"
    command = "/" + scene
    base_callback = scene + "/"
    choose_lesson = f"{base_callback}choose_lesson/"
    move_or_delete = f"{base_callback}move_or_delete/"
    type_date = State()
    choose_time = f"{base_callback}move/choose_time/"
    once_or_forever = f"{base_callback}once_or_forever/"
    choose_weekday = f"{base_callback}choose_weekday/"
    choose_recur_time = f"{base_callback}recur/choose_time/"
    type_recur_date = State()
    type_new_date = State()
    choose_recur_new_time = f"{base_callback}recur/new/choose_time/"

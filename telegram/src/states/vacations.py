from aiogram.fsm.state import State, StatesGroup


class Vacations(StatesGroup):
    scene = "vacations"
    text = "Расписание каникул"
    command = "/" + scene
    base_callback = scene + "/"
    remove_vacation = f"{base_callback}remove_vacation"
    add_vacation = f"{base_callback}add_vacation"
    choose_dates = State()

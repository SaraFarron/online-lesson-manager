from src.keyboards.builder import inline_keyboard


def choose_time(data: list[str], callback: str):
    buttons = {callback + str(t): str(t) for t in data}
    return inline_keyboard(buttons)


def choose_weekday(data: list[int], callback: str):
    buttons = {}
    for weekday in data:
        match weekday:
            case 0:
                buttons[callback + str(weekday)] = "Понедельник"
            case 1:
                buttons[callback + str(weekday)] = "Вторник"
            case 2:
                buttons[callback + str(weekday)] = "Среда"
            case 3:
                buttons[callback + str(weekday)] = "Четверг"
            case 4:
                buttons[callback + str(weekday)] = "Пятница"
            case 5:
                buttons[callback + str(weekday)] = "Суббота"
            case 6:
                buttons[callback + str(weekday)] = "Воскресенье"
    return inline_keyboard(buttons)


def choose_lesson(data: dict[int, str], callback: str):
    buttons = {callback + str(lesson_id): title for lesson_id, title in data.items()}
    return inline_keyboard(buttons)


def choose_move_or_delete(callback: str):
    buttons = {
        callback + "move": "Перенести",
        callback + "delete": "Отменить",
    }
    return inline_keyboard(buttons)

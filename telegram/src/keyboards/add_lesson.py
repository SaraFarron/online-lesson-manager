from src.keyboards.builder import inline_keyboard


def choose_time(data: list[str], callback: str):
    buttons = {callback + str(t): str(t) for t in data}
    return inline_keyboard(buttons)

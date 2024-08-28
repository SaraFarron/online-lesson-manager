from aiogram.utils.keyboard import InlineKeyboardBuilder

from online_lesson_manager.utils import get_weeks

builder = InlineKeyboardBuilder()

weekdays = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
for day in weekdays:
    builder.button(text=day, callback_data=f"set:{day}")

for week in get_weeks():
    for day in week:
        builder.button(text=day["date"], callback_data=f"set:{day}")

builder.adjust(7, repeat=True)

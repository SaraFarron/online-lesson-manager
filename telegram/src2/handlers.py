from aiogram import F, Router
from aiogram.types import Message

from .backend_client import BackendClient

router = Router()

backend_client = BackendClient(
    base_url="https://api.my-backend.com",
)


def format_schedule(data: dict) -> str:
    return "\n".join(
        f"{item['time']} — {item['subject']}"
        for item in data.get("items", [])
    )


@router.message(F.text == "📅 Расписание")
async def show_schedule(message: Message):
    schedule = await backend_client.get_schedule(teacher_id=1)

    if schedule is None:
        await message.answer("⚠️ Сервис временно недоступен")
        return

    text = format_schedule(schedule)

    if schedule.get("_stale"):
        await message.answer(
            "⚠️ Данные могут быть неактуальны\n\n" + text,
        )
    else:
        await message.answer(text)

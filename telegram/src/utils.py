from datetime import date, datetime

from sqlalchemy.orm import Session

from src.db.schemas import UserSchema
from src.service.dispatcher.check_work_breaks import schedule_work_break
from src.service.utils import send_message


async def auto_place_work_breaks(db: Session, user: UserSchema, day: datetime | date, executor_tg: int) -> None:
    if isinstance(day, datetime):
        day = day.date()
    work_breaks = schedule_work_break(db, user.executor_id, day)
    for brk in work_breaks:
        start_str = brk.start.strftime("%H:%M")
        await send_message(executor_tg, f"Автоматически добавлен перерыв на {start_str}")

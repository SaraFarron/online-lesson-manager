from datetime import datetime

from sqlalchemy.orm import Session

from src.db.schemas import UserSchema
from src.service.dispatcher.check_work_breaks import schedule_work_break
from src.service.utils import send_message


async def auto_place_work_breaks(db: Session, user: UserSchema, date: datetime, executor_tg: int) -> None:
    work_breaks = schedule_work_break(db, user.executor_id, date.date())
    for brk in work_breaks:
        start_str = brk.start.strftime("%H:%M")
        await send_message(executor_tg, f"Автоматически добавлен перерыв на {start_str}")

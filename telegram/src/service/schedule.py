from datetime import datetime

from db.models import User
from db.repositories import DBSession
from db.schemas import UserSchema
from interface.messages import replies
from service.services import EventService
from service.utils import day_schedule_text


class ScheduleService(DBSession):
    def day_schedule_prompt(self, user: UserSchema) -> str:
        lessons = EventService(self.db).day_schedule(
            user.executor_id,
            datetime.now().date(),
            None if user.role == User.Roles.TEACHER else user.id,
        )
        users_map = {
            u.id: f"@{u.username}" if u.username else f'<a href="tg://user?id={u.telegram_id}">{u.full_name}</a>'
            for u in self.db.query(User).filter(User.executor_id == user.executor_id)
        }
        result = day_schedule_text(lessons, users_map, user)
        return "\n".join(result) if result else replies.NO_LESSONS

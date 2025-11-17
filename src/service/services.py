from datetime import date, datetime, timedelta
from typing import Any

from aiogram.types import CallbackQuery, Message
from sqlalchemy import bindparam, text

from core.config import (
    CHANGE_DELTA,
    MAX_LESSONS_PER_DAY,
    SLOT_SIZE,
    TIME_FMT,
    WEEKDAY_MAP,
)
from db.models import CancelledRecurrentEvent, Event, EventHistory, Executor, HomeWork, RecurrentEvent, User
from db.repositories import DBSession, EventRepo, UserRepo
from db.schemas import EventSchema, RolesSchema
from interface.messages import replies
from service.utils import telegram_checks

HISTORY_MAP = {
    "help": "запросил помощь",
    "added_lesson": "добавил урок",
    "deleted_one_lesson": "удалил разовый урок",
    "deleted_recur_lesson": "удалил урок",
    "delete_vacation": "удалил каникулы",
    "added_vacation": "добавил каникулы",
    "recur_lesson_deleted": "разово отменил урок",
    "recur_lesson_moved": "перенес урок",
}


class UserService(DBSession):
    def check_user(self, event: Message | CallbackQuery, role: str = RolesSchema.STUDENT):
        message = telegram_checks(event)
        return self.check_user_with_id(event, message.from_user.id, role)

    def check_user_with_id(self, event: Message | CallbackQuery, user_id: int, role: str = RolesSchema.STUDENT):
        message = telegram_checks(event)
        user = UserRepo(self.db).get_by_telegram_id(user_id)
        if user is None:
            raise Exception("message", replies.PERMISSION_DENIED, "permission denied user is None")
        if role != RolesSchema.STUDENT and user.role != RolesSchema.TEACHER:
            raise Exception("message", replies.PERMISSION_DENIED, "user.role != Teacher")
        return message, user

    def register(self, tg_id: int, tg_full_name: str, tg_username: str, role: str, code: str):
        """Register a user."""
        ev = f"tg_id: {tg_id}, tg_full_name: {tg_full_name}, tg_username: {tg_username}, role: {role}, executor: {code}"
        event_log = EventHistory(
            author=tg_username,
            scene="start",
            event_type="register",
            event_value=ev,
        )

        executor = self.db.query(Executor).filter(Executor.code == code).first()
        if executor is None:
            raise Exception("message", "Произошла ошибка, скорее всего ссылка на бота неверна", "executor is None")

        user = User(
            telegram_id=tg_id,
            username=tg_username,
            full_name=tg_full_name,
            role=role,
            executor_id=executor.id,
        )
        self.db.add_all([user, event_log])
        self.db.commit()

    def delete(self, user_id: int):
        user = self.db.get(User, user_id)
        if user is None:
            raise Exception("message", "Пользователь не найден", f"user not found {user_id}")

        recur_events = self.db.query(RecurrentEvent).filter(RecurrentEvent.user_id == user_id)
        events = self.db.query(Event).filter(Event.user_id == user_id)
        username = user.username if user.username else user.full_name
        history = self.db.query(EventHistory).filter(EventHistory.author == username)
        event_breaks = self.db.query(CancelledRecurrentEvent).filter(
            CancelledRecurrentEvent.event_id.in_([re.id for re in recur_events]),
        )
        for e in list(event_breaks) + list(history) + list(recur_events) + list(events) + [user]:
            self.db.delete(e)
        self.db.commit()


class EventService(DBSession):
    LESSON_TYPES = (Event.EventTypes.LESSON, Event.EventTypes.MOVED_LESSON, RecurrentEvent.EventTypes.LESSON)

    def get_users_with_vacations(self, events: list, day: date):
        user_ids = [e.user_id for e in events]
        query = text("""
                    select start, end, user_id from events
                    where user_id in :user_ids and event_type = :event_type and start <= :today and end >= :today
                """).bindparams(bindparam("user_ids", expanding=True))
        vacations = list(
            self.db.execute(
                query,
                {
                    "user_ids": user_ids,
                    "event_type": Event.EventTypes.VACATION,
                    "today": datetime.combine(day, datetime.now().time()),
                },
            ),
        )
        return [v[2] for v in vacations]

    def day_schedule(self, executor_id: int, day: date, user_id: int | None = None):
        executor = self.db.get(Executor, executor_id)
        exec_user = self.db.query(User).filter(User.telegram_id == executor.telegram_id).first()
        repo = EventRepo(self.db)
        if repo.vacations_day(user_id, day) or repo.vacations_day(exec_user.id, day):
            return []
        ev = repo.events_for_day(executor_id, day)
        rv = repo.recurrent_events_for_day(executor_id, day)
        events = ev + rv
        users_with_vacations = self.get_users_with_vacations(events, day)
        events = list(filter(lambda x: x.user_id not in users_with_vacations, events))
        events = sorted(events, key=lambda x: x.start.time())
        if user_id is not None:
            events = list(filter(lambda x: x.user_id == user_id, events))
        return events

    def available_weekdays(self, executor_id: int):
        now = datetime.now()
        start_of_week = now.date() - timedelta(days=now.weekday())
        result = []
        repo = EventRepo(self.db)
        start_t, end_t = repo.get_work_start(executor_id)[0], repo.get_work_end(executor_id)[0]
        lesson_types = (Event.EventTypes.LESSON, Event.EventTypes.MOVED_LESSON, RecurrentEvent.EventTypes.LESSON)
        for i in range(7):
            current_day = start_of_week + timedelta(days=i)
            events = repo.recurrent_events_for_day(executor_id, current_day)
            lessons = [e for e in events if e.event_type in lesson_types]
            if len(lessons) >= MAX_LESSONS_PER_DAY:
                continue
            if RecurrentEvent.EventTypes.WEEKEND in [e.event_type for e in events]:
                continue
            available_time = repo.get_available_slots(start_t, end_t, SLOT_SIZE, events, current_day)
            if available_time:
                result.append(i)
        return result

    def get_day_events(self, executor_id: int, day: date):
        repo = EventRepo(self.db)
        events = repo.events_for_day(executor_id, day) + repo.recurrent_events_for_day(executor_id, day)
        users_with_vacations = self.get_users_with_vacations(events, day)
        return list(filter(lambda x: x.user_id not in users_with_vacations, events))

    def get_available_start_end(self, executor_id: int, day: date):
        repo = EventRepo(self.db)
        start, end = repo.get_work_start(executor_id)[0], repo.get_work_end(executor_id)[0]
        now = datetime.now()
        today_start = now + CHANGE_DELTA
        if today_start.time() > start and day == now.date():
            start = today_start.time()
        return start, end

    def available_time(self, executor_id: int, day: date):
        repo = EventRepo(self.db)
        if repo.vacations_day(executor_id, day):
            return [], []

        events = self.get_day_events(executor_id, day)
        lesson_types = (Event.EventTypes.LESSON, Event.EventTypes.MOVED_LESSON, RecurrentEvent.EventTypes.LESSON)
        lessons = [e for e in events if e.event_type in lesson_types]
        if len(lessons) >= MAX_LESSONS_PER_DAY:
            return [], []

        start, end = self.get_available_start_end(executor_id, day)
        available_slots = list(repo.get_available_slots(start, end, SLOT_SIZE, events, day))
        return [s[0] for s in available_slots], lessons

    def available_time_weekday(self, executor_id: int, weekday: int):
        now = datetime.now()
        repo = EventRepo(self.db)
        start_of_week = now.date() - timedelta(days=now.weekday())
        current_day = start_of_week + timedelta(days=weekday)
        events = repo.recurrent_events_for_day(executor_id, current_day)
        lessons = [e for e in events if e.event_type in self.LESSON_TYPES]

        start, end = repo.get_work_start(executor_id)[0], repo.get_work_end(executor_id)[0]
        simple_lessons = {}
        for s in repo.events_executor(executor_id):
            weekday_t = s.start.weekday()
            if s.event_type in self.LESSON_TYPES and s.start > now:
                if weekday_t not in simple_lessons:
                    simple_lessons[weekday_t] = []
                times = (
                    s.start,
                    s.start + timedelta(minutes=15),
                    s.start + timedelta(minutes=30),
                    s.start + timedelta(minutes=45),
                )
                for t in times:
                    simple_lessons[weekday_t].append(t.time())

        result = []
        for s in repo.get_available_slots(start, end, SLOT_SIZE, events, current_day):
            if s[0].weekday() in simple_lessons and s[0].time() in simple_lessons[s[0].weekday()]:
                continue
            result.append(s[0])
        return result, lessons

    def all_user_lessons(self, user: User):
        repo = EventRepo(self.db)
        recurs = repo.recurrent_events_executor(user.executor_id)
        events = repo.events_executor(user.executor_id)
        result = []
        for e in recurs + events:
            if e.event_type not in self.LESSON_TYPES or e.user_id != user.id:
                continue
            if isinstance(e, EventSchema) and e.end < datetime.now():
                continue
            result.append(e)
        return result

    def find_overlaps(self, weekdays: dict) -> set[Any]:
        overlaps = set()
        for events in weekdays.values():
            sorted_events = sorted(events, key=lambda x: x[0])

            for i in range(len(sorted_events)):
                event1 = sorted_events[i]
                for j in range(i + 1, len(sorted_events)):
                    event2 = sorted_events[j]
                    if event1[1] <= event2[0]:
                        break
                    seven, six = 7, 6
                    if len(event1) == seven and len(event2) == six:
                        e2_start, e2_end = (
                            datetime.combine(event2[4], event2[0]),
                            datetime.combine(event2[4], event2[1]),
                        )
                        c_start, c_end = datetime.combine(event2[4], event1[0]), datetime.combine(event2[4], event1[1])
                        if c_start <= e2_start <= c_end and c_start <= e2_end <= c_end:
                            break

                    overlap_pair = event1, event2
                    overlaps.add(overlap_pair)
        return overlaps

    def overlaps(self, executor_id: int) -> set[Any]:
        # Get all events
        repo = EventRepo(self.db)
        events = repo.events_executor(executor_id)
        rec_events = repo.recurrent_events_executor(executor_id)
        cancels = repo.recurrent_events_cancels(rec_events)
        cancel_map = {}
        for c in cancels:
            if c.event_id not in cancel_map:
                cancel_map[c.event_id] = []
            cancel_map[c.event_id].append((c.start, c.end))

        weekdays = {}
        for re in rec_events:
            weekday = re.start.weekday()
            if weekday not in weekdays:
                weekdays[weekday] = []
            start, end = re.start, re.end
            if re.id in cancel_map:
                cancel = cancel_map[re.id][0]  # len 7
                weekdays[weekday].append(
                    (start.time(), end.time(), re.user_id, re.id, re.event_type, cancel[0], cancel[1]),
                )
            else:
                weekdays[weekday].append((start.time(), end.time(), re.user_id, re.id, re.event_type))  # len 5

        for event in events:
            if event.event_type == Event.EventTypes.VACATION:
                continue
            weekday = event.start.weekday()
            if weekday not in weekdays:
                weekdays[weekday] = []
            start, end = event.start, event.end
            # len 6
            weekdays[weekday].append(
                (start.time(), end.time(), event.user_id, event.id, event.event_type, start.date()),
            )

        return self.find_overlaps(weekdays)

    def overlaps_match_case(self, ov1: list, ov2: list, rec_map: dict, users_map: dict) -> str | None:  # noqa: C901, PLR0911
        three = 3
        ov1t = str(ov1[0])
        if len(ov1t.split(":")) == three:
            ov1t = ov1t[:-3]
        ov2t = str(ov2[0])
        if len(ov2t.split(":")) == three:
            ov2t = ov2t[:-3]
        if ov1[4] == RecurrentEvent.EventTypes.WORK_BREAK:
            weekday = rec_map[ov1[3]].start.weekday()
            weekday = WEEKDAY_MAP[weekday]["long"]
            return f"{ov2[4]} в {ov2t} у {users_map[ov2[2]]} стоит в перерыв ({weekday})"
        if ov2[4] == RecurrentEvent.EventTypes.WORK_BREAK:
            weekday = rec_map[ov2[3]].start.weekday()
            weekday = WEEKDAY_MAP[weekday]["long"]
            return f"{ov1[4]} в {ov1t} у {users_map[ov1[2]]} стоит в перерыв ({weekday})"
        if ov1[4] == RecurrentEvent.EventTypes.WEEKEND:
            weekday = rec_map[ov1[3]].start.weekday()
            weekday = WEEKDAY_MAP[weekday]["long"]
            return f"{ov2[4]} в {ov2t} у {users_map[ov2[2]]} стоит в выходной ({weekday})"
        if ov2[4] == RecurrentEvent.EventTypes.WEEKEND:
            weekday = rec_map[ov2[3]].start.weekday()
            weekday = WEEKDAY_MAP[weekday]["long"]
            return f"{ov1[4]} в {ov1t} у {users_map[ov1[2]]} стоит в выходной ({weekday})"
        if ov1[4] == RecurrentEvent.EventTypes.WORK_START:
            work_start = rec_map[ov1[3]].end
            work_start = datetime.strftime(work_start, TIME_FMT)
            return f"{ov2[4]} в {ov2t} у {users_map[ov2[2]]} стоит до начала работы учителя ({work_start})"
        if ov2[4] == RecurrentEvent.EventTypes.WORK_START:
            work_start = rec_map[ov2[3]].end
            work_start = datetime.strftime(work_start, TIME_FMT)
            return f"{ov1[4]} в {ov1t} у {users_map[ov1[2]]} стоит до начала работы учителя ({work_start})"
        if ov1[4] == RecurrentEvent.EventTypes.WORK_END:
            work_end = rec_map[ov2[3]].start
            work_end = datetime.strftime(work_end, TIME_FMT)
            return f"{ov2[4]} в {ov2t} у {users_map[ov2[2]]} стоит после конца работы учителя ({work_end})"
        if ov2[4] == RecurrentEvent.EventTypes.WORK_END:
            work_end = rec_map[ov2[3]].start
            work_end = datetime.strftime(work_end, TIME_FMT)
            return f"{ov1[4]} в {ov1t} у {users_map[ov1[2]]} стоит после конца работы учителя ({work_end})"
        if ov1[4] == Event.EventTypes.VACATION or ov2[4] == Event.EventTypes.VACATION:
            return None
        umv1, umv2 = users_map[ov1[2]], users_map[ov2[2]]
        return f"Пересекаются уроки: {ov1[4]} в {ov1t} у {umv1} и {ov2[4]} в {ov2t} у {umv2}"

    def overlaps_text(self, overlaps: list[tuple]) -> list[Any]:
        texts = []
        user_overlap_map = {}
        for overlap in overlaps:
            ov1, ov2 = overlap[0], overlap[1]
            user_overlap_map[ov1[2]] = ov1
            user_overlap_map[ov2[2]] = ov2

        users_affected = self.db.query(User).filter(User.id.in_(list(user_overlap_map)))
        users_map = {
            u.id: u.username if u.username else u.full_name for u in users_affected if u.role == User.Roles.STUDENT
        }
        six = 6
        re_ids = [e[0][3] for e in overlaps if len(e) != six] + [e[1][3] for e in overlaps if len(e) != six]
        rec_map = {re.id: re for re in self.db.query(RecurrentEvent).filter(RecurrentEvent.id.in_(re_ids))}

        for overlap in overlaps:
            ov1, ov2 = overlap[0], overlap[1]
            if ov2[2] not in users_map and ov1[2] not in users_map:
                continue
            row_text = self.overlaps_match_case(ov1, ov2, rec_map, users_map)
            if row_text is None:
                continue
            texts.append(row_text)

        return texts

    def overlaps_messages(self, overlaps: list[tuple]) -> dict[Any, Any]:  # noqa: C901, PLR0912, PLR0915
        user_overlap_map = {}
        for overlap in overlaps:
            ov1, ov2 = overlap[0], overlap[1]
            user_overlap_map[ov1[2]] = ov1
            user_overlap_map[ov2[2]] = ov2

        users_affected = self.db.query(User).filter(User.id.in_(list(user_overlap_map)))
        users_map = {
            u.id: (u.username if u.username else u.full_name, u.telegram_id)
            for u in users_affected
            if u.role == User.Roles.STUDENT
        }
        six = 6
        re_ids = [e[0][3] for e in overlaps if len(e) != six] + [e[1][3] for e in overlaps if len(e) != six]
        rec_map = {re.id: re for re in self.db.query(RecurrentEvent).filter(RecurrentEvent.id.in_(re_ids))}
        messages = {}
        three = 3
        for overlap in overlaps:
            ov1, ov2 = overlap[0], overlap[1]
            if ov2[2] not in users_map and ov1[2] not in users_map:
                continue
            ov1t = str(ov1[0])
            if len(ov1t.split(":")) == three:
                ov1t = ov1t[:-3]
            ov2t = str(ov2[0])
            if len(ov2t.split(":")) == three:
                ov2t = ov2t[:-3]
            if ov1[4] == RecurrentEvent.EventTypes.WORK_BREAK:
                weekday = rec_map[ov1[3]].start.weekday()
                weekday = WEEKDAY_MAP[weekday]["long"]
                row_text = f"{ov2[4]} в {ov2t} стоит в перерыв ({weekday})"
                user_tg = users_map[ov2[2]][1]
            elif ov2[4] == RecurrentEvent.EventTypes.WORK_BREAK:
                weekday = rec_map[ov2[3]].start.weekday()
                weekday = WEEKDAY_MAP[weekday]["long"]
                row_text = f"{ov1[4]} в {ov1t} стоит в перерыв ({weekday})"
                user_tg = users_map[ov1[2]][1]
            elif ov1[4] == RecurrentEvent.EventTypes.WEEKEND:
                weekday = rec_map[ov1[3]].start.weekday()
                weekday = WEEKDAY_MAP[weekday]["long"]
                row_text = f"{ov2[4]} в {ov2t} стоит в выходной ({weekday})"
                user_tg = users_map[ov2[2]][1]
            elif ov2[4] == RecurrentEvent.EventTypes.WEEKEND:
                weekday = rec_map[ov2[3]].start.weekday()
                weekday = WEEKDAY_MAP[weekday]["long"]
                row_text = f"{ov1[4]} в {ov1t} стоит в выходной ({weekday})"
                user_tg = users_map[ov1[2]][1]
            elif ov1[4] == RecurrentEvent.EventTypes.WORK_START:
                work_start = rec_map[ov1[3]].end
                work_start = datetime.strftime(work_start, TIME_FMT)
                row_text = f"{ov2[4]} в {ov2t} стоит до начала работы учителя ({work_start})"
                user_tg = users_map[ov2[2]][1]
            elif ov2[4] == RecurrentEvent.EventTypes.WORK_START:
                work_start = rec_map[ov2[3]].end
                work_start = datetime.strftime(work_start, TIME_FMT)
                row_text = f"{ov1[4]} в {ov1t} стоит до начала работы учителя ({work_start})"
                user_tg = users_map[ov1[2]][1]
            elif ov1[4] == RecurrentEvent.EventTypes.WORK_END:
                work_end = rec_map[ov2[3]].start
                work_end = datetime.strftime(work_end, TIME_FMT)
                row_text = f"{ov2[4]} в {ov2t} стоит после конца работы учителя ({work_end})"
                user_tg = users_map[ov2[2]][1]
            elif ov2[4] == RecurrentEvent.EventTypes.WORK_END:
                work_end = rec_map[ov2[3]].start
                work_end = datetime.strftime(work_end, TIME_FMT)
                row_text = f"{ov1[4]} в {ov1t} стоит после конца работы учителя ({work_end})"
                user_tg = users_map[ov1[2]][1]
            else:
                continue

            if user_tg not in messages:
                messages[user_tg] = []
            messages[user_tg].append(row_text)

        return messages

    def vacations(self, user_id: int):
        return EventRepo(self.db).vacations(user_id)

    def work_schedule(self, executor_id: int):
        repo = EventRepo(self.db)
        return repo.work_hours(executor_id), repo.weekends(executor_id)

    def work_breaks(self, executor_id: int):
        return EventRepo(self.db).work_breaks(executor_id)

    def delete_work_hour(self, executor_id: int, kind: str):
        return EventRepo(self.db).delete_work_hour_setting(executor_id, kind)

    def available_work_weekdays(self, executor_id: int):
        return EventRepo(self.db).available_work_weekdays(executor_id)

    def cancel_event(self, event_id: int):
        return EventRepo(self.db).cancel_event(event_id)


class HomeWorkService(DBSession):
    def homeworks(self, user: User):
        return self.db.query(HomeWork).filter(HomeWork.user_id == user.id)

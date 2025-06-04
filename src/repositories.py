from datetime import date, datetime, time, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.config import (
    CHANGE_DELTA,
    DB_DATETIME,
    LESSON_SIZE,
    MAX_LESSONS_PER_DAY,
    SLOT_SIZE,
    TIME_FMT,
    WEEKDAY_MAP,
)
from src.models import CancelledRecurrentEvent, Event, EventHistory, Executor, RecurrentEvent, User


class Repo:
    def __init__(self, db: Session):
        self.db = db


class UserRepo(Repo):
    @property
    def roles(self):
        return User.Roles

    def get_by_telegram_id(self, telegram_id: int, raise_error: bool = False):
        """Retrieve a user by telegram id."""
        user = self.db.query(User).filter(User.telegram_id == telegram_id).first()
        if user is None and raise_error:
            raise Exception("message", "У вас нет прав на эту команду", "permission denied user is None")
        return user

    def register(self, tg_id: int, tg_full_name: str, tg_username: str, role: str, code: str):
        """Register a user."""
        event_log = EventHistory(
            author=tg_username,
            scene="start",
            event_type="register",
            event_value=f"tg_id: {tg_id}, tg_full_name: {tg_full_name}, tg_username: {tg_username}, role: {role}, executor: {code}",
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
        event_breaks = self.db.query(CancelledRecurrentEvent).filter(CancelledRecurrentEvent.event_id.in_([re.id for re in recur_events]))
        for e in list(event_breaks) + list(history) + list(recur_events) + list(events) + [user]:
            self.db.delete(e)
        self.db.commit()

    def executor_telegram_id(self, user: User):
        executor = self.db.get(Executor, user.executor_id)
        return executor.telegram_id


class EventHistoryRepo(Repo):
    def create(self, author: str, scene: str, event_type: str, event_value: str):
        log = EventHistory(
            author=author,
            scene=scene,
            event_type=event_type,
            event_value=event_value,
        )
        self.db.add(log)
        self.db.commit()

    def user_history(self, username: str):
        events = self.db.execute(
            text("""
                select created_at, scene, event_type, event_value from event_history
                where author = :author
                order by created_at desc
                limit 10
            """),
            {"author": username},
        )
        return list(events)


class EventRepo(Repo):
    LESSON_TYPES = (Event.EventTypes.LESSON, Event.EventTypes.MOVED_LESSON, RecurrentEvent.EventTypes.LESSON)

    def _events_executor(self, executor_id: int):
        today = datetime.now().date()
        return list(
            self.db.execute(
                text("""
                        select start, end, user_id, event_type, is_reschedule, id from events
                        where executor_id = :executor_id and start >= :today and cancelled is false
                        order by start
                """),
                {"executor_id": executor_id, "today": today},
            ),
        )

    def _recurrent_events_executor(self, executor_id: int):
        return list(
            self.db.execute(
                text("""
                        select start, end, user_id, event_type, interval, interval_end, id from recurrent_events
                        where executor_id = :executor_id
                        order by start
                """),
                {"executor_id": executor_id},
            ),
        )

    def recurrent_events_cancels(self, events: list[tuple]):
        if events:
            return list(
                self.db.execute(
                    text("""
                select event_id, break_type, start, end from event_breaks
                where event_id in (:event_ids)
            """),
                    {"event_ids": ",".join(str(e[-1]) for e in events)},
                ),
            )
        return []

    def recurrent_events(self, executor_id: int):
        events = self._recurrent_events_executor(executor_id)
        cancellations = self.recurrent_events_cancels(events)
        return events, cancellations

    def recurrent_events_for_day(self, executor_id: int, day: date):
        events, cancels = self.recurrent_events(executor_id)
        result = []
        for event in events:
            start_dt, end_dt, user_id, event_type, interval, interval_end, event_id = event
            start_dt = datetime.strptime(start_dt, DB_DATETIME)
            end_dt = datetime.strptime(end_dt, DB_DATETIME)

            # Skip if event recurrence has ended before our target date
            if interval_end and interval_end.date() < day:
                continue

            # Calculate the time difference between original start and target date
            days_diff = (day - start_dt.date()).days

            # Check if this event should occur on target_date based on interval
            if interval > 0 and days_diff % interval == 0:
                # Calculate the exact datetime on target_date
                event_time = start_dt.time()
                event_start = datetime.combine(day, event_time)
                event_end = event_start + (end_dt - start_dt)

                # Check if this occurrence is cancelled
                is_cancelled = False
                for cancel in cancels:
                    c_event_id, break_type, c_start, c_end = cancel

                    # Skip if cancellation is for a different event
                    if c_event_id != event_id:
                        continue

                    # Check if cancellation overlaps with this event occurrence
                    if (event_start < c_end) and (event_end > c_start):
                        is_cancelled = True
                        break

                if not is_cancelled:
                    result.append((event_start, event_end, user_id, event_type, False))
        return result

    def events_for_day(self, executor_id: int, day: date):
        start, end = self.get_work_start(executor_id)[0], self.get_work_end(executor_id)[0]
        day_start = datetime.combine(day, start)
        day_end = datetime.combine(day, end)
        events = self.db.execute(text("""
            select start, end, user_id, event_type, is_reschedule from events
            where executor_id = :executor_id and start >= :day_start and end <= :day_end and cancelled is false
            order by start desc
        """), {"executor_id": executor_id, "day_start": day_start, "day_end": day_end})
        result = []
        for e in events:
            start_dt = datetime.strptime(e[0], DB_DATETIME)
            end_dt = datetime.strptime(e[1], DB_DATETIME)
            result.append((start_dt, end_dt, *e[2:]))
        return result

    def day_schedule(self, executor_id: int, day: date, user_id: int | None = None):
        if self.vacations_day(user_id, day):
            return []
        events = self.events_for_day(executor_id, day) + self.recurrent_events_for_day(executor_id, day)
        events = sorted(events, key=lambda x: x[0])
        if user_id is not None:
            events = list(filter(lambda x: x[2] == user_id, events))
        return events

    def available_weekdays(self, executor_id: int):
        start_of_week = datetime.now().date() - timedelta(days=datetime.now().weekday())
        result = []
        start_t, end_t = self.get_work_start(executor_id)[0], self.get_work_end(executor_id)[0]
        lesson_types = (Event.EventTypes.LESSON, Event.EventTypes.MOVED_LESSON, RecurrentEvent.EventTypes.LESSON)
        for i in range(7):
            current_day = start_of_week + timedelta(days=i)
            events = self.recurrent_events_for_day(executor_id, current_day)
            lessons = [e for e in events if e[3] in lesson_types]
            if len(lessons) >= MAX_LESSONS_PER_DAY:
                continue
            start = datetime.combine(current_day, start_t)
            end = datetime.combine(current_day, end_t)
            available_time = self._get_available_slots(start, end, SLOT_SIZE, events)
            if available_time:
                result.append(i)
        return result

    def available_time(self, executor_id: int, day: date):
        events = self.events_for_day(executor_id, day) + self.recurrent_events_for_day(executor_id, day)
        lesson_types = (Event.EventTypes.LESSON, Event.EventTypes.MOVED_LESSON, RecurrentEvent.EventTypes.LESSON)
        lessons = [e for e in events if e[3] in lesson_types]
        if len(lessons) >= MAX_LESSONS_PER_DAY:
            return []

        start, end = self.get_work_start(executor_id)[0], self.get_work_end(executor_id)[0]
        start = datetime.combine(day, start)
        end = datetime.combine(day, end)
        now = datetime.now()
        result = []
        for slot in self._get_available_slots(start, end, SLOT_SIZE, events):
            if day == now.date() and now + CHANGE_DELTA > slot[0]:
                continue
            result.append(slot[0])
        return result

    def available_time_weekday(self, executor_id: int, weekday: int):
        start_of_week = datetime.now().date() - timedelta(days=datetime.now().weekday())
        current_day = start_of_week + timedelta(days=weekday)
        events = self.recurrent_events_for_day(executor_id, current_day)

        start, end = self.get_work_start(executor_id)[0], self.get_work_end(executor_id)[0]
        start = datetime.combine(current_day, start)
        end = datetime.combine(current_day, end)
        return [s[0] for s in self._get_available_slots(start, end, SLOT_SIZE, events)]

    @staticmethod
    def _get_available_slots(start: datetime, end: datetime, slot_size: timedelta, events: list):
        # Generate all slots (15-minute increments)
        all_slots = []
        current_slot = start

        while current_slot + LESSON_SIZE <= end:  # Check for full 1-hour availability
            all_slots.append((current_slot, current_slot + LESSON_SIZE))  # 1-hour slot
            current_slot += slot_size  # Move by 15 minutes

        # Function to check if a 1-hour slot overlaps with any occupied period
        def is_occupied(slot):
            slot_start, slot_end = slot
            for occupied in events:
                occupied_start = (
                    datetime.strptime(occupied[0], DB_DATETIME) if isinstance(occupied[0], str) else occupied[0]
                )
                occupied_end = (
                    datetime.strptime(occupied[1], DB_DATETIME) if isinstance(occupied[1], str) else occupied[1]
                )
                if not (slot_end <= occupied_start or slot_start >= occupied_end):
                    return True  # The slot is occupied
            return False  # The slot is available

        # Filter out occupied slots
        return [slot for slot in all_slots if not is_occupied(slot)]

    def all_user_lessons(self, user: User):
        recurs = self._recurrent_events_executor(user.executor_id)
        events = self._events_executor(user.executor_id)
        result = []
        for e in recurs + events:
            if e.event_type not in self.LESSON_TYPES or e.user_id != user.id:
                continue
            result.append(e)
        return result

    def cancel_event(self, event_id: int):
        event = self.db.get(Event, event_id)
        if event:
            event.cancelled = True
            self.db.add(event)
            self.db.commit()
            return event
        raise Exception("message", "Урок не найден", f"event with id {event_id} does not exist")

    def work_hours(self, executor_id: int):
        events = self._recurrent_events_executor(executor_id)
        work_hours = filter(
            lambda x: x.event_type in (RecurrentEvent.EventTypes.WORK_START, RecurrentEvent.EventTypes.WORK_END),
            events,
        )
        return list(work_hours)

    def delete_work_hour_setting(self, executor_id: int, kind: str):
        if kind == "end":
            event_time, event = self.get_work_end(executor_id)
        elif kind == "start":
            event_time, event = self.get_work_start(executor_id)
        else:
            raise Exception("message", "Неизвестный тип события", f"unknown kind: {kind}")
        self.db.delete(event)
        self.db.commit()
        return event_time

    def get_work_end(self, executor_id: int):
        event = self.db.query(RecurrentEvent).filter(
            RecurrentEvent.executor_id == executor_id,
            RecurrentEvent.event_type == RecurrentEvent.EventTypes.WORK_END,
        ).first()
        if event:
            return event.start.time(), event
        return time(hour=20, minute=0), None

    def get_work_start(self, executor_id: int):
        event = self.db.query(RecurrentEvent).filter(
            RecurrentEvent.executor_id == executor_id,
            RecurrentEvent.event_type == RecurrentEvent.EventTypes.WORK_START,
        ).first()
        if event:
            return event.end.time(), event
        return time(hour=9, minute=0), None

    def weekends(self, executor_id: int):
        events = self._recurrent_events_executor(executor_id)
        weekends = filter(
            lambda x: x.event_type == RecurrentEvent.EventTypes.WEEKEND,
            events,
        )
        return list(weekends)

    def available_work_weekdays(self, executor_id: int):
        weekends = []
        for weekend in self.weekends(executor_id):
            if not isinstance(weekend.start, datetime):
                start = datetime.strptime(weekend.start, DB_DATETIME)
            else:
                start = weekend.start
            weekends.append(start.weekday())
        return [i for i in range(7) if i not in weekends]

    def vacations(self, user_id: int):
        events = self.db.execute(
            text("""
                select start, end, id from events
                where user_id = :user_id and event_type = :vacation and cancelled is false
            """),
            {"user_id": user_id, "vacation": Event.EventTypes.VACATION},
        )
        return list(events)

    def vacations_day(self, user_id: int, day: date):
        events = self.vacations(user_id)
        if not events:
            return False
        for event in events:
            start = datetime.strptime(event.start, DB_DATETIME)
            end = datetime.strptime(event.end, DB_DATETIME)
            if start.date() <= day <= end.date():
                return True
        return False

    def work_breaks(self, executor_id: int):
        events = self._recurrent_events_executor(executor_id)
        if events:
            events = list(filter(lambda x: x.event_type == RecurrentEvent.EventTypes.WORK_BREAK, events))
        return events

    def overlaps(self, executor_id: int):
        # Get all events
        events = self._events_executor(executor_id)
        rec_events = self._recurrent_events_executor(executor_id)
        cancels = self.recurrent_events_cancels(rec_events)
        cancel_map = {}
        for c in cancels:
            if c.event_id not in cancel_map:
                cancel_map[c.event_id] = []
            cancel_map[c.event_id].append((c.start, c.end))

        weekdays = {}
        for re in rec_events:
            dt = datetime.strptime(re.start, DB_DATETIME)
            weekday = dt.weekday()
            if weekday not in weekdays:
                weekdays[weekday] = []
            start, end = datetime.strptime(re.start, DB_DATETIME), datetime.strptime(re.end, DB_DATETIME)
            if re.id in cancel_map:
                cancel = cancel_map[re.id]  # len 7
                weekdays[weekday].append(
                    (start.time(), end.time(), re.user_id, re.id, re.event_type, cancel[0], cancel[1]),
                )
            else:
                weekdays[weekday].append((start.time(), end.time(), re.user_id, re.id, re.event_type))  # len 5

        for event in events:
            dt = datetime.strptime(event.start, DB_DATETIME)
            weekday = dt.weekday()
            if weekday not in weekdays:
                weekdays[weekday] = []
            start, end = datetime.strptime(event.start, DB_DATETIME), datetime.strptime(event.end, DB_DATETIME)
            # len 6
            weekdays[weekday].append(
                (start.time(), end.time(), event.user_id, event.id, event.event_type, start.date()),
            )

        overlaps = set()
        for weekday, events in weekdays.items():
            sorted_events = sorted(events, key=lambda x: x[0])

            for i in range(len(sorted_events)):
                event1 = sorted_events[i]
                for j in range(i + 1, len(sorted_events)):
                    event2 = sorted_events[j]
                    if event1[1] <= event2[0]:
                        break
                    if len(event1) == 7:
                        if len(event2) == 6:
                            e2_start, e2_end = datetime.combine(event2[4], event2[0]), datetime.combine(event2[4], event2[1])
                            c_start, c_end = datetime.combine(event2[4], event1[0]), datetime.combine(event2[4], event1[1])
                            if c_start <= e2_start <= c_end and c_start <= e2_end <= c_end:
                                break

                    overlap_pair = event1, event2
                    overlaps.add(overlap_pair)

        return overlaps

    def overlaps_text(self, overlaps: list[tuple]):
        texts = []
        user_overlap_map = {}
        for overlap in overlaps:
            ov1, ov2 = overlap[0], overlap[1]
            user_overlap_map[ov1[2]] = ov1
            user_overlap_map[ov2[2]] = ov2

        users_affected = self.db.query(User).filter(User.id.in_(list(user_overlap_map)))
        users_map = {u.id: u.username if u.username else u.full_name for u in users_affected if u.role == User.Roles.STUDENT}
        re_ids = [e[0][3] for e in overlaps if len(e) != 6] + [e[1][3] for e in overlaps if len(e) != 6]
        rec_map = {re.id: re for re in self.db.query(RecurrentEvent).filter(RecurrentEvent.id.in_(re_ids))}

        for overlap in overlaps:
            ov1, ov2 = overlap[0], overlap[1]
            if ov2[2] not in users_map and ov1[2] not in users_map:
                continue
            ov1t = str(ov1[0])
            if len(ov1t.split(":")) == 3:
                ov1t = ov1t[:-3]
            ov2t = str(ov2[0])
            if len(ov2t.split(":")) == 3:
                ov2t = ov2t[:-3]
            if ov1[4] == RecurrentEvent.EventTypes.WORK_BREAK:
                weekday = rec_map[ov1[3]].start.weekday()
                weekday = WEEKDAY_MAP[weekday]["long"]
                row_text = f"{ov2[4]} в {ov2t} у {users_map[ov2[2]]} стоит в перерыв ({weekday})"
            elif ov2[4] == RecurrentEvent.EventTypes.WORK_BREAK:
                weekday = rec_map[ov2[3]].start.weekday()
                weekday = WEEKDAY_MAP[weekday]["long"]
                row_text = f"{ov1[4]} в {ov1t} у {users_map[ov1[2]]} стоит в перерыв ({weekday})"
            elif ov1[4] == RecurrentEvent.EventTypes.WEEKEND:
                weekday = rec_map[ov1[3]].start.weekday()
                weekday = WEEKDAY_MAP[weekday]["long"]
                row_text = f"{ov2[4]} в {ov2t} у {users_map[ov2[2]]} стоит в выходной ({weekday})"
            elif ov2[4] == RecurrentEvent.EventTypes.WEEKEND:
                weekday = rec_map[ov2[3]].start.weekday()
                weekday = WEEKDAY_MAP[weekday]["long"]
                row_text = f"{ov1[4]} в {ov1t} у {users_map[ov1[2]]} стоит в выходной ({weekday})"
            elif ov1[4] == RecurrentEvent.EventTypes.WORK_START:
                work_start = rec_map[ov1[3]].end
                work_start = datetime.strftime(work_start, TIME_FMT)
                row_text = f"{ov2[4]} в {ov2t} у {users_map[ov2[2]]} стоит до начала работы учителя ({work_start})"
            elif ov2[4] == RecurrentEvent.EventTypes.WORK_START:
                work_start = rec_map[ov2[3]].end
                work_start = datetime.strftime(work_start, TIME_FMT)
                row_text = f"{ov1[4]} в {ov1t} у {users_map[ov1[2]]} стоит до начала работы учителя ({work_start})"
            elif ov1[4] == RecurrentEvent.EventTypes.WORK_END:
                work_end = rec_map[ov2[3]].start
                work_end = datetime.strftime(work_end, TIME_FMT)
                row_text = f"{ov2[4]} в {ov2t} у {users_map[ov2[2]]} стоит после конца работы учителя ({work_end})"
            elif ov2[4] == RecurrentEvent.EventTypes.WORK_END:
                work_end = rec_map[ov2[3]].start
                work_end = datetime.strftime(work_end, TIME_FMT)
                row_text = f"{ov1[4]} в {ov1t} у {users_map[ov1[2]]} стоит после конца работы учителя ({work_end})"
            elif ov1[4] == Event.EventTypes.VACATION or ov2[4] == Event.EventTypes.VACATION:
                continue
            else:
                row_text = f"Пересекаются уроки: {ov1[4]} в {ov1t} у {users_map[ov1[2]]} и {ov2[4]} в {ov2t} у {users_map[ov2[2]]}"
            texts.append(row_text)

        return "Замечены несостыковки\n" + "\n".join(texts)

    def overlaps_messages(self, overlaps: list[tuple]):
        user_overlap_map = {}
        for overlap in overlaps:
            ov1, ov2 = overlap[0], overlap[1]
            user_overlap_map[ov1[2]] = ov1
            user_overlap_map[ov2[2]] = ov2

        users_affected = self.db.query(User).filter(User.id.in_(list(user_overlap_map)))
        users_map = {
            u.id: (u.username if u.username else u.full_name, u.telegram_id) for u in users_affected if u.role == User.Roles.STUDENT
        }
        re_ids = [e[0][3] for e in overlaps if len(e) != 6] + [e[1][3] for e in overlaps if len(e) != 6]
        rec_map = {re.id: re for re in self.db.query(RecurrentEvent).filter(RecurrentEvent.id.in_(re_ids))}
        messages = {}
        for overlap in overlaps:
            ov1, ov2 = overlap[0], overlap[1]
            if ov2[2] not in users_map and ov1[2] not in users_map:
                continue
            ov1t = str(ov1[0])
            if len(ov1t.split(":")) == 3:
                ov1t = ov1t[:-3]
            ov2t = str(ov2[0])
            if len(ov2t.split(":")) == 3:
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

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.scene import Scene, on
from aiogram.types import KeyboardButton, Message, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.orm import Session

from config import config, help
from database import engine
from models import RestrictedTime, ScheduledLesson, User


@dataclass
class Answer:
    """Represents an answer to a question."""

    text: str
    callback_data: str | None = None


def possible_weekdays(user_telegram_id: int) -> list[Answer]:
    """Return a list of available weekdays."""
    result = []
    with Session(engine) as session:
        user = session.query(User).filter(User.telegram_id == user_telegram_id).first()
        if not user:
            print("NO USER")
            return []
        for weekday in config.WEEKDAYS:
            # Check if any restrictions for this day
            restriced_periods = (
                session.query(RestrictedTime)
                .filter(
                    RestrictedTime.weekday == weekday,
                    RestrictedTime.user == user,
                )
                .all()
            )
            if any(period.whole_day_restricted for period in restriced_periods):
                continue
            result.append(Answer(text=weekday, callback_data=f"createsl:{weekday}"))
        return result


def possible_times(user_telegram_id: int, weekday: Literal["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"]) -> list[Answer]:
    """Return a list of available times."""
    result = []
    with Session(engine) as session:
        user = session.query(User).filter(User.telegram_id == user_telegram_id).first()
        if not user:
            print("NO USER")
            return []

        # Check if any restrictions for this day
        restriced_periods = (
            session.query(RestrictedTime)
            .filter(
                RestrictedTime.weekday == weekday,
                RestrictedTime.user == user,
            )
            .all()
        )
        taken_times = [(period.start_time, period.end_time) for period in restriced_periods]

        # Check if any lessons for this day
        lessons_this_day = session.query(ScheduledLesson).filter(ScheduledLesson.weekday == weekday).all()
        for lesson in lessons_this_day:
            taken_times.append((lesson.start_time, lesson.end_time))  # noqa: PERF401

        # Forming buttons for available time
        current_time: datetime = user.teacher.work_start
        while current_time < user.teacher.work_end:
            taken = False
            for taken_time in taken_times:
                if taken_time[0] <= current_time < taken_time[1]:
                    taken = True
                    break
            if not taken:
                result.append(
                    Answer(
                        text=current_time.strftime("%H:%M"),
                        callback_data=f"createsl:{current_time.strftime('%H.%M')}",
                    ),
                )
            current_time += timedelta(hours=1)

    return result


# TODO complete this scene
BACK = "🔙 Назад"
CANCEL = "🚫 Отмена"


class CreateScheduledLesson(Scene, state="create_scheduled_lesson"):
    MESSAGES = [
        "Выберите день недели",
        "Выберите время",
    ]

    @on.message.enter()
    async def on_enter(self, message: Message, state: FSMContext, step: int | None = 0):
        """Method triggered when the user enters the create_scheduled_lesson scene."""
        if not step:
            await message.answer("Выберите день недели")

        if step > 1:
            return await self.wizard.exit()

        markup = InlineKeyboardBuilder()
        if step == 0:
            markup.add(*[
                InlineKeyboardButton(text=answer.text) for answer in possible_weekdays(message.from_user.id)
            ])
        else:
            state_data = await state.get_data()
            weekday = state_data["weekday"]
            markup.add(*[
                InlineKeyboardButton(text=answer.text) for answer in possible_times(message.from_user.id, weekday)
            ])

        if step > 0:
            markup.button(text=BACK)
        markup.button(text=CANCEL)

        await state.update_data(step=step)
        return await message.answer(
            text=self.MESSAGES[step],
            reply_markup=markup.adjust(2).as_markup(resize_keyboard=True),
        )

    @on.message.exit()
    async def on_exit(self, message: Message, state: FSMContext) -> None:
        """Method triggered when the user exits the quiz scene."""
        await message.answer("Ваше расписание создано!")
        await state.set_data({})

    @on.message(F.text == BACK)
    async def back(self, message: Message, state: FSMContext) -> None:
        """
        Method triggered when the user selects the "Back" button.

        It allows the user to go back to the previous question.
        """
        data = await state.get_data()
        step = data["step"]

        previous_step = step - 1
        if previous_step < 0:
            # In case when the user tries to go back from the first question,
            # we just exit the scene
            return await self.wizard.exit()
        return await self.wizard.back(step=previous_step)

    @on.message(F.text == CANCEL)
    async def exit(self, message: Message) -> None:
        """Method triggered when the user selects the "Exit" button."""
        await self.wizard.exit()

    @on.message(F.text)
    async def answer(self, message: Message, state: FSMContext) -> None:
        """
        Method triggered when the user selects an answer.

        It stores the answer and proceeds to the next question.

        :param message:
        :param state:
        :return:
        """
        data = await state.get_data()
        step = data["step"]
        answers = data.get("answers", {})
        answers[step] = message.text
        await state.update_data(answers=answers)

        await self.wizard.retake(step=step + 1)

    @on.message()
    async def unknown_message(self, message: Message) -> None:
        """
        Method triggered when the user sends a message that is not a command or an answer.

        It asks the user to select an answer.

        :param message: The message received from the user.
        :return: None
        """
        await message.answer("Произошло что то непонятное.")


create_lesson_router = Router(name=__name__)
# Add handler that initializes the scene
create_lesson_router.message.register(CreateScheduledLesson.as_handler(), Command("create_scheduled_lesson"))
create_lesson_router.message.register(CreateScheduledLesson.as_handler(), F.text == help.CREATE_SCHEDULED_LESSON)

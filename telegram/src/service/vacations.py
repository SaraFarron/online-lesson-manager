from datetime import datetime

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.keyboards import edit_vacations
from src.messages import replies
from src.schemas import EventCreate
from src.service.base import BaseService
from src.states import Vacations
from src.utils import get_callback_arg


class VacationsService(BaseService):
    def __init__(
        self,
        message: Message | CallbackQuery,
        state: FSMContext,
        callback: CallbackQuery | None = None,
    ) -> None:
        super().__init__(message, state, callback)

    async def vacations_list(self):
        try:
            vacs = await self.backend_client.get_vacations(self.telegram_id)
        except Exception as e:
            await self.message.answer(str(e))
            await self.state.clear()
            return

        await self.message.answer(
            "Расписание каникул",
            reply_markup=edit_vacations(vacs, Vacations.add_vacation, Vacations.remove_vacation),
        )

    async def get_dates(self):
        await self.message.answer(replies.CHOOSE_DATES)
        await self.state.set_state(Vacations.choose_dates)

    async def add_vacation(self):
        dates = await self.check_date_range(Vacations.choose_dates)
        if not dates:
            return

        user_token = await self.get_user_token()
        if not user_token:
            return

        start_date, end_date = dates
        start = datetime.combine(start_date, datetime.min.time())
        end = datetime.combine(end_date, datetime.max.time())
        duration = (end - start).days * 24 * 60  # duration in minutes
        vacation = EventCreate(
            title="vacation",
            day=start_date,
            start=datetime.min.time(),
            duration=duration,
            is_recurrent=False,
        )
        try:
            await self.backend_client.create_event(vacation, token=user_token)
        except Exception as e:
            await self.message.answer(str(e))
            await self.state.clear()
            return

        await self.message.answer(replies.VACATION_ADDED)
        await self.state.clear()

    async def remove_vacation(self):
        event_id = int(get_callback_arg(self.callback.data, Vacations.remove_vacation))
        if not event_id:
            await self.message.answer(replies.SOMETHING_WENT_WRONG)
            await self.state.clear()
            return
        await self._delete_event(
            event_id=event_id,
            not_found=replies.VACATION_NOT_FOUND_ERR,
            success=replies.VACATION_DELETED,
        )

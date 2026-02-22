from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.keyboards import vacations
from src.service.base import BaseService
from src.states import Vacations


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

        if not vacs:
            await self.message.answer("Каникул нет")
            await self.state.clear()
            return

        await self.message.answer(
            "Расписание каникул",
            reply_markup=vacations(vacs, Vacations.add_vacation, Vacations.remove_vacation),
        )

    async def get_dates(self):
        pass
    
    async def add_vacation(self):
        pass
    
    async def remove_vacation(self):
        pass
    
    
    

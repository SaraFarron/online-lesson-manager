from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from src.service.backend_client import BackendClient


class ScheduleService:
    def __init__(self, message: Message, state: FSMContext) -> None:
        self.message = message
        self.state = state
        self.backend_client = BackendClient()
        if not message.from_user:
            raise ValueError("Message must have a from_user attribute")
        self.telegram_id = message.from_user.id
        self.username = message.from_user.username

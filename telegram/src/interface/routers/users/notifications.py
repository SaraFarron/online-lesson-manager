import asyncio

import aiohttp
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ContentType, Message
from sqlalchemy.orm import Session

from src.core.config import BOT_TOKEN
from src.core.middlewares import DatabaseMiddleware
from src.db.models import User
from src.db.schemas import RolesSchema
from src.interface.keyboards import AdminCommands
from src.interface.messages import replies
from src.service.services import UserService

router = Router()


router.callback_query.middleware(DatabaseMiddleware())
# Temporary storage for media groups
media_group_storage = {}

class Notifications(StatesGroup):
    scene = "notifications"
    command = "/" + scene
    base_callback = scene + "/"
    notification = State()


@router.message(Command(Notifications.command))
@router.message(F.text == AdminCommands.SEND_TO_EVERYONE.value)
async def notifications_handler(message: Message, state: FSMContext, db: Session) -> None:
    message, user = UserService(db).check_user(message, RolesSchema.TEACHER)
    await state.update_data(user_id=user.telegram_id)
    await state.set_state(Notifications.notification)
    await message.answer(replies.SEND_NOTIFICATION)


@router.message(Notifications.notification)
async def notification(message: Message, state: FSMContext, db: Session) -> None:
    state_data = await state.get_data()
    message, user = UserService(db).check_user_with_id(message, state_data["user_id"], RolesSchema.TEACHER)

    students = list(db.query(User).filter(User.executor_id == user.executor_id))
    receivers, errors = await TelegramMessages().send_complex_message(message, students)
    receivers = receivers if isinstance(receivers, int) else len(receivers)
    if receivers == len(students):
        await message.answer(f"Сообщение отправлено {receivers} ученикам.")
    if errors:
        await message.answer("Не удалось отправить сообщение ученикам:\n" + ", ".join(errors))
    await state.clear()


class TelegramMessages:
    async def send_message(self, telegram_id: int, username: str, message: Message):
        attempt, max_attempts = 0, 3
        while attempt < max_attempts:
            try:
                if message.content_type == ContentType.TEXT:
                    await self.send_text_message(telegram_id, message.text)
                elif message.content_type == ContentType.PHOTO:
                    await self.send_photo_message(telegram_id, message.photo[-1].file_id, message.caption)
                elif message.content_type == ContentType.VIDEO:
                    await self.send_video_message(telegram_id, message.video.file_id, message.caption)
                else:
                    await message.answer(replies.UNSUPPORTED_MEDIA_TYPE)
                return True
            except Exception as e:
                print(f"Attempt {attempt + 1} failed to send message to {username}: {e}")
                attempt += 1
                await asyncio.sleep(1)  # Wait before retrying
        return False
    
    async def send_complex_message(self, message: Message, students: list):
        # Handle media groups
        if message.media_group_id:
            if message.media_group_id not in media_group_storage:
                media_group_storage[message.media_group_id] = []
            media_group_storage[message.media_group_id].append(message)

            # Set a timer to process the group if no new items arrive
            await asyncio.sleep(2)  # Wait 2 seconds for all group items
            if message.media_group_id in media_group_storage:
                return await self.process_media_group(message.media_group_id, students)
            return 0

        # Handle single messages
        receivers, errors = [], []
        for student in students:
            success = await self.send_message(student.telegram_id, student.username, message)
            if success:
                receivers.append(student.username)
            else:
                errors.append(student.username)
        return receivers, errors

    async def send_media_group(self, telegram_id: int, media_messages: list[Message]) -> None:
        """Send a media group (album) to a user"""
        # Prepare media group
        media_group = []
        combined_caption = None

        for msg in media_messages:
            # Get the caption from the first message that has one
            if msg.caption and combined_caption is None:
                combined_caption = msg.caption

            if msg.content_type == ContentType.PHOTO:
                media = {
                    "type": "photo",
                    "media": msg.photo[-1].file_id,
                }
            elif msg.content_type == ContentType.VIDEO:
                media = {
                    "type": "video",
                    "media": msg.video.file_id,
                }
            else:
                continue

            media_group.append(media)

        # Add caption only to the first media item if exists
        if combined_caption and media_group:
            media_group[0]["caption"] = combined_caption
            media_group[0]["parse_mode"] = "HTML"

        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMediaGroup"
        data = {"chat_id": telegram_id, "media": media_group}

        async with aiohttp.ClientSession() as session, session.post(url, json=data) as resp:
            response = await resp.json()
            if not response.get("ok"):
                print(f"Failed to send media group: {response}")
            return response

    async def process_media_group(self, group_id: str, students: list):
        """Process a complete media group for all students"""
        if group_id not in media_group_storage:
            return 0

        messages = media_group_storage.pop(group_id)

        if not messages:
            return 0

        # Send to all students
        success_count = 0
        for student in students:
            try:
                response = await self.send_media_group(student.telegram_id, messages)
                if response.get("ok"):
                    success_count += 1
            except Exception as e:
                print(f"Failed to send media group to {student.username}: {e}")

        return success_count

    async def send_text_message(self, telegram_id: int, text: str) -> None:
        """Send a text message to the user."""
        text = text.replace("\n", "%0A")
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage?chat_id={telegram_id}&text={text}&parse_mode=HTML"
        async with aiohttp.ClientSession() as session, session.get(url) as resp:
            await resp.text()

    async def send_photo_message(self, telegram_id: int, photo_file_id: str, caption: str | None = None) -> None:
        """Send a photo message to the user."""
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto?chat_id={telegram_id}&photo={photo_file_id}"
        if caption:
            caption = caption.replace("\n", "%0A")
            url += f"&caption={caption}&parse_mode=HTML"
        async with aiohttp.ClientSession() as session, session.get(url) as resp:
            await resp.text()

    async def send_video_message(self, telegram_id: int, video_file_id: str, caption: str | None = None) -> None:
        """Send a video message to the user."""
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo?chat_id={telegram_id}&video={video_file_id}"
        if caption:
            caption = caption.replace("\n", "%0A")
            url += f"&caption={caption}&parse_mode=HTML"
        async with aiohttp.ClientSession() as session, session.get(url) as resp:
            await resp.text()

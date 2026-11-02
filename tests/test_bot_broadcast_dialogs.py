from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
from aiogram import Dispatcher
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.models import StudentProfile, TeacherProfile, User
from backend.notifications.constants import AcknowledgementOutcome
from backend.notifications.models import NotificationIntent, TeacherBroadcast
from backend.notifications.schemas import AcknowledgeRequest
from backend.notifications.service import acknowledge_intent, claim_pending_intents
from telegram.api import BackendClient
from telegram.runtime import build_dispatcher
from tests.bot_support import BotDriver, rendered_utf16_length

TEACHER_TELEGRAM_ID = 6_200_000_001
STUDENT_TELEGRAM_ID = 6_200_001_000
BROADCAST_ROOT = "/api/v1/notifications/broadcasts"


async def _seed(
    session: AsyncSession, *, student_count: int = 2
) -> tuple[User, list[User]]:
    teacher_id = uuid4()
    teacher = User(
        id=teacher_id,
        email=f"broadcast-dialog-teacher-{teacher_id}@example.com",
        full_name="Broadcast Teacher",
        role="TEACHER",
        timezone="UTC",
        telegram_user_id=TEACHER_TELEGRAM_ID,
        telegram_private_chat_id=TEACHER_TELEGRAM_ID,
        is_active=True,
    )
    students = [
        User(
            id=uuid4(),
            email=f"broadcast-dialog-student-{index}-{teacher_id}@example.com",
            full_name=f"Broadcast Student {index:02}",
            role="STUDENT",
            timezone="UTC",
            teacher_id=teacher_id,
            telegram_user_id=STUDENT_TELEGRAM_ID + index,
            telegram_private_chat_id=STUDENT_TELEGRAM_ID + index,
            is_active=True,
        )
        for index in range(student_count)
    ]
    session.add_all(
        [
            teacher,
            *students,
            TeacherProfile(teacher_id=teacher_id, code=f"t_{teacher_id.hex[:6]}"),
            *(StudentProfile(student_id=student.id) for student in students),
        ]
    )
    await session.commit()
    return teacher, students


async def _make_driver(
    client: httpx.AsyncClient,
    bot_service_key: str,
    *,
    user_id: int = TEACHER_TELEGRAM_ID,
    lose_publish_response: bool = False,
    dispatcher: Dispatcher | None = None,
    unavailable_preview: bool = False,
) -> tuple[BotDriver, BackendClient, httpx.AsyncClient, list[httpx.Request]]:
    requests: list[httpx.Request] = []
    lost_response = False

    async def transport(request: httpx.Request) -> httpx.Response:
        nonlocal lost_response
        requests.append(request)
        if (
            unavailable_preview
            and request.method == "POST"
            and request.url.path == f"{BROADCAST_ROOT}/preview"
        ):
            raise httpx.ConnectError("simulated backend outage", request=request)
        response = await client.send(request)
        if (
            lose_publish_response
            and not lost_response
            and request.method == "POST"
            and request.url.path == BROADCAST_ROOT
            and response.status_code == 202
        ):
            lost_response = True
            raise httpx.ReadTimeout(
                "publish response lost after commit", request=request
            )
        return response

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url="http://test"
    )
    api = BackendClient(http, bot_service_key)
    driver = BotDriver(dispatcher or build_dispatcher(api), user_id=user_id)
    return driver, api, http, requests


async def _close(driver: BotDriver, http: httpx.AsyncClient) -> None:
    await driver.close()
    await http.aclose()


async def test_preview_cancel_is_nonpublishing_and_broadcast_is_teacher_only(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    bot_service_key: str,
) -> None:
    _teacher, students = await _seed(db_session)
    teacher_driver, _api, teacher_http, requests = await _make_driver(
        client, bot_service_key
    )
    student_driver, _student_api, student_http, _student_requests = await _make_driver(
        client,
        bot_service_key,
        user_id=STUDENT_TELEGRAM_ID,
        dispatcher=teacher_driver.dispatcher,
    )
    try:
        await student_driver.text("/start")
        await student_driver.click("Settings")
        student_screen_text = student_driver.session.last_message.text
        student_screen_id = student_driver.session.last_message.message_id

        await teacher_driver.text("/start")
        await teacher_driver.click("Broadcast")
        await teacher_driver.text("<Hello & welcome> 💌")
        await teacher_driver.click("Preview")

        assert "&lt;Hello &amp; welcome&gt; 💌" in (
            teacher_driver.session.last_message.text
        )
        assert "Recipients: 2; routable: 2; unsendable: 0." in (
            teacher_driver.session.last_message.text
        )
        assert not any(request.url.path == BROADCAST_ROOT for request in requests)
        assert (
            await db_session.scalar(select(func.count()).select_from(TeacherBroadcast))
            == 0
        )
        assert (
            await db_session.scalar(
                select(func.count()).select_from(NotificationIntent)
            )
            == 0
        )

        await teacher_driver.click("Cancel")
        assert student_driver.session.last_message.text == student_screen_text
        assert student_driver.session.last_message.message_id == student_screen_id
        await student_driver.click("Back to home")
        assert student_driver.session.last_message.text != student_screen_text
        markup = student_driver.session.last_message.reply_markup
        assert markup is not None
        assert all(
            button.text != "Broadcast"
            for row in markup.inline_keyboard
            for button in row
        )
        assert len(students) == 2
    finally:
        await _close(teacher_driver, teacher_http)
        await _close(student_driver, student_http)


async def test_confirmation_reconciles_lost_response_and_results_reload_and_page(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    bot_service_key: str,
) -> None:
    _teacher, students = await _seed(db_session, student_count=12)
    unrelated_teacher_id = uuid4()
    unrelated_teacher = User(
        id=unrelated_teacher_id,
        email=f"broadcast-dialog-unrelated-teacher-{unrelated_teacher_id}@example.com",
        full_name="Unrelated Teacher",
        role="TEACHER",
        timezone="UTC",
        telegram_user_id=6_200_000_002,
        telegram_private_chat_id=6_200_000_002,
        is_active=True,
    )
    unrelated_student_id = uuid4()
    unrelated_student = User(
        id=unrelated_student_id,
        email=f"broadcast-dialog-unrelated-student-{unrelated_student_id}@example.com",
        full_name="Unrelated Student",
        role="STUDENT",
        timezone="UTC",
        teacher_id=unrelated_teacher_id,
        telegram_user_id=6_200_003_000,
        telegram_private_chat_id=6_200_003_000,
        is_active=True,
    )
    db_session.add_all([unrelated_teacher, unrelated_student])
    await db_session.commit()
    driver, api, http, requests = await _make_driver(
        client,
        bot_service_key,
        lose_publish_response=True,
    )
    student_driver, _student_api, student_http, _student_requests = await _make_driver(
        client,
        bot_service_key,
        user_id=STUDENT_TELEGRAM_ID,
    )
    try:
        await student_driver.text("/start")
        student_home_text = student_driver.session.last_message.text
        student_home_id = student_driver.session.last_message.message_id
        await driver.text("/start")
        await driver.click("Broadcast")
        await driver.text("Please read the update.")
        await driver.click("Preview")
        assert "Preview only — nothing has been sent." in (
            driver.session.last_message.text
        )
        assert "routable: 12" in driver.session.last_message.text

        await driver.click("Confirm broadcast")
        assert "publish outcome is unknown" in driver.session.last_message.text
        driver.callback_data("Check publication status")
        assert (
            await db_session.scalar(select(func.count()).select_from(TeacherBroadcast))
            == 1
        )
        assert (
            await db_session.scalar(
                select(func.count()).select_from(NotificationIntent)
            )
            == 12
        )
        queued_intents = list(await db_session.scalars(select(NotificationIntent)))
        assert {intent.recipient_id for intent in queued_intents} == {
            student.id for student in students
        }
        publication = await db_session.scalar(select(TeacherBroadcast))
        assert publication is not None
        assert all(
            intent.notification_type == "TEACHER_BROADCAST"
            and intent.source_id == publication.id
            for intent in queued_intents
        )
        assert unrelated_student.id not in {
            intent.recipient_id for intent in queued_intents
        }

        await driver.click("Check publication status")
        assert "Queued for delivery" in driver.session.last_message.text
        assert "Pending: 12" in driver.session.last_message.text
        assert "Broadcast Student 00: Pending" in driver.session.last_message.text
        driver.callback_data("Next results")
        await driver.click("Next results")
        assert "Page 2 of 2" in driver.session.last_message.text
        assert "Broadcast Student 10: Pending" in driver.session.last_message.text

        claimed = await claim_pending_intents(
            db_session,
            now=datetime.now(UTC) + timedelta(seconds=1),
            limit=1,
        )
        assert len(claimed) == 1
        await acknowledge_intent(
            db_session,
            claimed[0].id,
            AcknowledgeRequest(
                lease_token=claimed[0].lease_token,
                outcome=AcknowledgementOutcome.SENT,
            ),
            now=datetime.now(UTC) + timedelta(seconds=2),
        )
        await driver.click("Refresh results")
        assert "sent: 1" in driver.session.last_message.text
        assert "Pending: 11" in driver.session.last_message.text
        assert (
            len(
                [
                    request
                    for request in requests
                    if request.method == "POST" and request.url.path == BROADCAST_ROOT
                ]
            )
            == 1
        )
        assert (
            await db_session.scalar(select(func.count()).select_from(TeacherBroadcast))
            == 1
        )
        assert len(students) == 12
        assert len(api.pending_broadcast_operations) == 0
        assert student_driver.session.last_message.text == student_home_text
        assert student_driver.session.last_message.message_id == student_home_id
        assert (
            sum(call.__api_method__ == "sendMessage" for call in driver.session.calls)
            == 1
        )
    finally:
        await _close(driver, http)
        await _close(student_driver, student_http)


async def test_stale_preview_returns_to_editable_draft(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    bot_service_key: str,
) -> None:
    teacher, _students = await _seed(db_session)
    driver, _api, http, _requests = await _make_driver(client, bot_service_key)
    try:
        await driver.text("/start")
        await driver.click("Broadcast")
        await driver.text("Roster-sensitive text")
        await driver.click("Preview")
        db_session.add(
            User(
                id=uuid4(),
                email="broadcast-dialog-new-student@example.com",
                full_name="New Student",
                role="STUDENT",
                timezone="UTC",
                teacher_id=teacher.id,
                telegram_user_id=6_200_002_000,
                telegram_private_chat_id=6_200_002_000,
                is_active=True,
            )
        )
        await db_session.commit()

        await driver.click("Confirm broadcast")

        assert "Preview the broadcast again" in driver.session.last_message.text
        assert "Roster-sensitive text" in driver.session.last_message.text
        assert (
            await db_session.scalar(select(func.count()).select_from(TeacherBroadcast))
            == 0
        )
    finally:
        await _close(driver, http)


async def test_maximum_broadcast_preview_is_escaped_and_length_safe(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    bot_service_key: str,
) -> None:
    await _seed(db_session, student_count=1)
    driver, _api, http, _requests = await _make_driver(client, bot_service_key)
    try:
        await driver.text("/start")
        await driver.click("Broadcast")
        await driver.text("&" * 4096)
        await driver.click("Preview")

        first_page = driver.session.last_message.text
        assert "Message (page 1 of 4)" in first_page
        assert "&amp;" in first_page
        assert rendered_utf16_length(first_page) < 4096

        await driver.click("Next message")
        second_page = driver.session.last_message.text
        assert "Message (page 2 of 4)" in second_page
        assert rendered_utf16_length(second_page) < 4096
    finally:
        await _close(driver, http)


async def test_backend_outage_during_preview_keeps_broadcast_unpublished(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    bot_service_key: str,
) -> None:
    await _seed(db_session, student_count=2)
    driver, _api, http, requests = await _make_driver(
        client,
        bot_service_key,
        unavailable_preview=True,
    )
    try:
        await driver.text("/start")
        await driver.click("Broadcast")
        await driver.text("A message to preview")
        await driver.click("Preview")

        assert "unavailable" in driver.session.last_message.text.casefold()
        assert any(
            request.url.path == f"{BROADCAST_ROOT}/preview" for request in requests
        )
        assert (
            await db_session.scalar(select(func.count()).select_from(TeacherBroadcast))
            == 0
        )
        assert (
            await db_session.scalar(
                select(func.count()).select_from(NotificationIntent)
            )
            == 0
        )
    finally:
        await _close(driver, http)

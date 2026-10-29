import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.models import User
from backend.constants import EventType
from backend.events.booking_policy import prepare_lesson_write
from backend.events.models import Event, RecurrentCancels, RecurrentEvent
from backend.events.mutations.displacement import apply_displacements
from backend.events.mutations.serialization import run_serialized_schedule_mutation
from backend.events.revisions import (
    _assert_expected_revision,
    build_event_revision,
)
from backend.events.schemas import EventCreate, EventUpdate
from backend.events.time_utils import to_storage_utc
from backend.events.utils import assert_can_assign_user, assert_owns_event
from backend.events.validators import EventValidator
from backend.exceptions import ValidationError


async def create_event(
    session: AsyncSession,
    event_data: EventCreate,
    validator: EventValidator,
    teacher_id: uuid.UUID,
    *,
    confirm_displacement: bool = False,
    displacement_token: str | None = None,
    actor_is_teacher: bool = False,
) -> Event:
    return await run_serialized_schedule_mutation(
        session,
        teacher_id,
        lambda: create_event_in_transaction(
            session,
            event_data,
            validator,
            teacher_id,
            confirm_displacement=confirm_displacement,
            displacement_token=displacement_token,
            actor_is_teacher=actor_is_teacher,
        ),
    )


async def create_event_in_transaction(
    session: AsyncSession,
    event_data: EventCreate,
    validator: EventValidator,
    teacher_id: uuid.UUID,
    *,
    confirm_displacement: bool = False,
    displacement_token: str | None = None,
    actor_is_teacher: bool = False,
    commit: bool = True,
    post_break_minutes: int | None = None,
) -> Event:
    teacher_profile = None
    teacher_timezone = None
    saved_post_break_minutes = 0
    if event_data.event_type == EventType.LESSON:
        event_data, teacher_profile, teacher_timezone = await prepare_lesson_write(
            session,
            event_data,
            teacher_id,
            actor_is_teacher=actor_is_teacher,
        )
        if teacher_profile is not None:
            saved_post_break_minutes = teacher_profile.between_lessons_break
        if post_break_minutes is not None:
            saved_post_break_minutes = post_break_minutes
    if event_data.end is None:
        raise ValidationError("Event end time is required")

    displaced = await validator.validate_all(
        session,
        event_data,
        teacher_id=teacher_id,
        user_id=event_data.user_id,
        confirm_displacement=confirm_displacement,
        displacement_token=displacement_token,
        teacher_profile=teacher_profile,
        teacher_timezone=teacher_timezone,
        post_break_minutes=saved_post_break_minutes,
    )
    await apply_displacements(session, displaced, teacher_id)

    event = Event(
        start=to_storage_utc(event_data.start),
        end=to_storage_utc(event_data.end),
        user_id=event_data.user_id,
        teacher_id=teacher_id,
        event_type=event_data.event_type,
        post_break_minutes=saved_post_break_minutes,
    )
    session.add(event)
    if commit:
        await session.commit()
        await session.refresh(event)
    else:
        await session.flush()
    return event


async def update_event(
    session: AsyncSession,
    event: Event,
    event_data: EventUpdate,
    validator: EventValidator,
    teacher_id: uuid.UUID,
    *,
    actor: User | None = None,
    expected_revision: str | None = None,
    confirm_displacement: bool = False,
    displacement_token: str | None = None,
    actor_is_teacher: bool = False,
) -> Event:
    return await run_serialized_schedule_mutation(
        session,
        teacher_id,
        lambda: _update_event(
            session,
            event,
            event_data,
            validator,
            teacher_id,
            actor=actor,
            expected_revision=expected_revision,
            confirm_displacement=confirm_displacement,
            displacement_token=displacement_token,
            actor_is_teacher=actor_is_teacher,
        ),
    )


async def _update_event(
    session: AsyncSession,
    event: Event,
    event_data: EventUpdate,
    validator: EventValidator,
    teacher_id: uuid.UUID,
    *,
    actor: User | None = None,
    expected_revision: str | None = None,
    confirm_displacement: bool = False,
    displacement_token: str | None = None,
    actor_is_teacher: bool = False,
) -> Event:
    fresh_event = await session.get(Event, event.id, populate_existing=True)
    if fresh_event is None or fresh_event.teacher_id != teacher_id:
        raise ValidationError("Lesson or event not found")
    event = fresh_event
    if actor is not None:
        assert_owns_event(actor, event)
        await assert_can_assign_user(session, actor, event_data.user_id)
    if expected_revision is not None:
        _assert_expected_revision(expected_revision, build_event_revision(event, ()))

    teacher_profile = None
    teacher_timezone = None
    if validator.expected_event_type == EventType.LESSON:
        event_data, teacher_profile, teacher_timezone = await prepare_lesson_write(
            session,
            event_data,
            teacher_id,
            actor_is_teacher=actor_is_teacher,
        )
    if event_data.end is None:
        raise ValidationError("Event end time is required")

    displaced = await validator.validate_all(
        session,
        event_data,
        teacher_id=teacher_id,
        user_id=event_data.user_id,
        exclude_event_id=event.id,
        confirm_displacement=confirm_displacement,
        displacement_token=displacement_token,
        teacher_profile=teacher_profile,
        teacher_timezone=teacher_timezone,
        post_break_minutes=event.post_break_minutes,
    )
    await apply_displacements(session, displaced, teacher_id)

    event.start = to_storage_utc(event_data.start)
    event.end = to_storage_utc(event_data.end)
    event.user_id = event_data.user_id
    event.teacher_id = teacher_id
    session.add(event)
    await session.commit()
    await session.refresh(event)
    return event


async def delete_event(
    session: AsyncSession,
    event: Event | RecurrentEvent,
    *,
    actor: User | None = None,
    expected_revision: str | None = None,
) -> None:
    await run_serialized_schedule_mutation(
        session,
        event.teacher_id,
        lambda: _delete_event(session, event, actor, expected_revision),
    )


async def _delete_event(
    session: AsyncSession,
    event: Event | RecurrentEvent,
    actor: User | None,
    expected_revision: str | None,
) -> None:
    if isinstance(event, RecurrentEvent):
        fresh_recurrent = await session.get(
            RecurrentEvent, event.id, populate_existing=True
        )
        if fresh_recurrent is None or fresh_recurrent.teacher_id != event.teacher_id:
            raise ValidationError("Event not found")
        if actor is not None:
            assert_owns_event(actor, fresh_recurrent)
        cancellations = list(
            (
                await session.scalars(
                    select(RecurrentCancels)
                    .where(RecurrentCancels.recurrent_event_id == fresh_recurrent.id)
                    .order_by(RecurrentCancels.start, RecurrentCancels.id)
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        if expected_revision is not None:
            _assert_expected_revision(
                expected_revision,
                build_event_revision(fresh_recurrent, cancellations),
            )
        await session.execute(
            delete(RecurrentCancels).where(
                RecurrentCancels.recurrent_event_id == fresh_recurrent.id
            )
        )
        await session.delete(fresh_recurrent)
    else:
        fresh_one_off = await session.get(Event, event.id, populate_existing=True)
        if fresh_one_off is None or fresh_one_off.teacher_id != event.teacher_id:
            raise ValidationError("Event not found")
        if actor is not None:
            assert_owns_event(actor, fresh_one_off)
        if expected_revision is not None:
            _assert_expected_revision(
                expected_revision, build_event_revision(fresh_one_off, ())
            )
        await session.delete(fresh_one_off)
    await session.commit()

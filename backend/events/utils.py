import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.constants import Roles
from backend.auth.models import User
from backend.auth.service import get_user_by_id
from backend.events.models import Event, RecurrentEvent
from backend.exceptions import BadRequest, Forbidden


def resolve_teacher_id(user: User) -> uuid.UUID:
    """Resolve the teacher_id an event should be scheduled under for this user.

    Teachers act under their own id; students act under their linked teacher's id.
    """
    if user.role == Roles.TEACHER:
        return user.id
    if user.role == Roles.STUDENT and user.teacher_id is not None:
        return user.teacher_id
    raise BadRequest("User is not linked to a teacher")


async def assert_can_assign_user(
    session: AsyncSession, actor: User, user_id: uuid.UUID
) -> None:
    """Ensure `actor` may create/reassign an event for `user_id` (self, or own student)."""
    if actor.role == Roles.TEACHER:
        if user_id == actor.id:
            return
        target = await get_user_by_id(session, user_id)
        if (
            target is not None
            and target.role == Roles.STUDENT
            and target.teacher_id == actor.id
        ):
            return
        raise Forbidden("User does not belong to this teacher")
    if actor.role == Roles.STUDENT:
        if user_id == actor.id:
            return
        raise Forbidden("Students can only manage their own events")
    raise Forbidden("Only teachers and students can manage schedule events")


def assert_teacher_actor(actor: User) -> None:
    if actor.role != Roles.TEACHER:
        raise Forbidden("Only teachers can manage breaks")


def assert_owns_event(actor: User, event: Event | RecurrentEvent) -> None:
    """Ensure the actor owns the subject event in the appropriate teacher scope."""
    if actor.role == Roles.TEACHER:
        if event.teacher_id != actor.id:
            raise Forbidden("You do not have access to this event")
        return
    if actor.role == Roles.STUDENT:
        if (
            actor.teacher_id is None
            or event.teacher_id != actor.teacher_id
            or event.user_id != actor.id
        ):
            raise Forbidden("You do not have access to this event")
        return
    raise Forbidden("You do not have access to this event")

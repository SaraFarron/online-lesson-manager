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
    if user.teacher_id is not None:
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
        if target is not None and target.teacher_id == actor.id:
            return
        raise Forbidden("User does not belong to this teacher")
    if user_id != actor.id:
        raise Forbidden("Students can only manage their own events")


def assert_owns_event(
    resolved_teacher_id: uuid.UUID, event: Event | RecurrentEvent
) -> None:
    """Ensure the event belongs to the caller's teacher scope before update/delete."""
    if event.teacher_id != resolved_teacher_id:
        raise Forbidden("You do not have access to this event")

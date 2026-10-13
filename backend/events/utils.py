import uuid

from backend.auth.constants import Roles
from backend.auth.models import User
from backend.exceptions import BadRequest


def resolve_teacher_id(user: User) -> uuid.UUID:
    """Resolve the teacher_id an event should be scheduled under for this user.

    Teachers act under their own id; students act under their linked teacher's id.
    """
    if user.role == Roles.TEACHER:
        return user.id
    if user.teacher_id is not None:
        return user.teacher_id
    raise BadRequest("User is not linked to a teacher")

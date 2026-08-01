import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlmodel import col, func, select

from app.api.deps import CurrentUser, SessionDep
from app.models import (
    Timetable, TimetableRows
)

router = APIRouter(prefix="/timetable", tags=["timetable"])


@router.get("/", response_model=Timetable)
def read_timetable(session: SessionDep, current_user: CurrentUser, start: datetime, end: datetime) -> Any:
    """
    Get event by ID.
    """
    if current_user.is_superuser:
        count_statement = (
            select(func.count())
            .select_from(TimetableRows)
            .where(TimetableRows.start >= start & TimetableRows.end <= end)
        )
        count = session.exec(count_statement).one()
        statement = (
            select(TimetableRows).order_by(col(TimetableRows.created_at)
        )
        timetable_rows = session.exec(statement).all()
    else:
        count_statement = (
            select(func.count())
            .select_from(TimetableRows)
            .where(
                ((TimetableRows.teacher_id == current_user.id) | (TimetableRows.student_id == current_user.id)) & \
                (TimetableRows.start >= start & TimetableRows.end <= end)
            )
        )
        count = session.exec(count_statement).one()
        statement = (
            select(TimetableRows)
            .where(
                ((TimetableRows.teacher_id == current_user.id) | (TimetableRows.student_id == current_user.id)) & \
                (TimetableRows.start >= start & TimetableRows.end <= end)
            )
            .order_by(col(TimetableRows.created_at).desc())
        )
        timetable_rows = session.exec(statement).all()

    result = [TimetableRows.model_validate(item) for item in timetable_rows]
    return Timetable(data=result, count=count)

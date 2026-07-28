import uuid
from datetime import UTC, datetime

from pydantic import EmailStr
from sqlalchemy import DateTime
from sqlmodel import Field, Relationship, SQLModel


def get_datetime_utc() -> datetime:
    return datetime.now(UTC)


class Base(SQLModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: datetime | None = Field(default_factory=get_datetime_utc, sa_type=DateTime(timezone=True))  # ty:ignore[invalid-argument-type]


# Generic message
class Message(SQLModel):
    message: str


# JSON payload containing access token
class Token(SQLModel):
    access_token: str
    token_type: str = "bearer"


# Contents of JWT token
class TokenPayload(SQLModel):
    sub: str | None = None


class NewPassword(SQLModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


# Shared properties
class UserBase(SQLModel):
    email: EmailStr = Field(unique=True, index=True, max_length=255)
    is_active: bool = True
    is_superuser: bool = False
    is_teacher: bool = False
    full_name: str | None = Field(default=None, max_length=255)


# Properties to receive via API on creation
class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)


class UserRegister(SQLModel):
    email: EmailStr = Field(max_length=255)
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)


# Properties to receive via API on update, all are optional
class UserUpdate(SQLModel):
    email: EmailStr | None = Field(default=None, max_length=255)
    is_active: bool | None = None
    is_superuser: bool | None = None
    is_teacher: bool | None = None
    full_name: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, min_length=8, max_length=128)


class UserUpdateMe(SQLModel):
    full_name: str | None = Field(default=None, max_length=255)
    email: EmailStr | None = Field(default=None, max_length=255)


class UpdatePassword(SQLModel):
    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


# Database model, database table inferred from class name
class User(Base, UserBase, table=True):
    hashed_password: str
    events_as_teacher: list[Event] = Relationship(back_populates="teacher", cascade_delete=True)
    events_as_student: list[Event] = Relationship(back_populates="student", cascade_delete=True)
    recurrent_events_as_teacher: list[RecurrentEvent] = Relationship(back_populates="teacher", cascade_delete=True)
    recurrent_events_as_student: list[RecurrentEvent] = Relationship(back_populates="student", cascade_delete=True)


# Properties to return via API, id is always required
class UserPublic(UserBase):
    id: uuid.UUID
    created_at: datetime | None = None


class UsersPublic(SQLModel):
    data: list[UserPublic]
    count: int


# Shared properties
class EventBase(SQLModel):
    title: str = Field(min_length=1, max_length=255)
    start: datetime
    end: datetime
    teacher_id: uuid.UUID = Field(foreign_key="user.id", nullable=False, ondelete="CASCADE")
    student_id: uuid.UUID = Field(foreign_key="user.id", nullable=False, ondelete="CASCADE")


# Properties to receive on item creation
class EventCreate(EventBase):
    pass


# Properties to receive on item update
class EventUpdate(SQLModel):
    title: str = Field(min_length=1, max_length=255)
    start: datetime
    end: datetime


# Database model, database table inferred from class name
class Event(Base, EventBase, table=True):
    teacher: User = Relationship(
        back_populates="events_as_teacher", sa_relationship_kwargs={"foreign_keys": "Event.teacher_id"}
    )
    student: User = Relationship(
        back_populates="events_as_student", sa_relationship_kwargs={"foreign_keys": "Event.student_id"}
    )


class RecurrentEventBase(EventBase):
    interval: int = Field(default=1, ge=1)


class RecurrentEventCreate(RecurrentEventBase):
    pass


class RecurrentEventUpdate(SQLModel):
    title: str = Field(min_length=1, max_length=255)
    start: datetime
    end: datetime
    interval: int = Field(default=1, ge=1)


class RecurrentEvent(Base, RecurrentEventBase, table=True):
    teacher: User = Relationship(
        back_populates="recurrent_events_as_teacher",
        sa_relationship_kwargs={"foreign_keys": "RecurrentEvent.teacher_id"},
    )
    student: User = Relationship(
        back_populates="recurrent_events_as_student",
        sa_relationship_kwargs={"foreign_keys": "RecurrentEvent.student_id"},
    )


class DocumentBase(SQLModel):
    name: str = Field(min_length=1, max_length=255)
    path: str = Field(min_length=1, max_length=1024)
    homework_id: uuid.UUID = Field(foreign_key="homework.id", nullable=False, ondelete="CASCADE")


class DocumentCreate(DocumentBase):
    pass


class Document(Base, DocumentBase, table=True):
    homework: Homework = Relationship(back_populates="documents")


class HomeworkBase(SQLModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1024)
    due_date: datetime
    teacher_id: uuid.UUID = Field(foreign_key="user.id", nullable=False, ondelete="CASCADE")
    student_id: uuid.UUID = Field(foreign_key="user.id", nullable=False, ondelete="CASCADE")
    lesson_id: uuid.UUID | None = Field(default=None, foreign_key="event.id", nullable=True, ondelete="CASCADE")
    recurrent_lesson_id: uuid.UUID | None = Field(
        default=None, foreign_key="recurrentevent.id", nullable=True, ondelete="CASCADE"
    )


class HomeworkCreate(HomeworkBase):
    pass


class HomeworkUpdate(SQLModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1024)
    due_date: datetime | None = None
    lesson_id: uuid.UUID | None = Field(default=None, foreign_key="event.id", nullable=True, ondelete="CASCADE")
    recurrent_lesson_id: uuid.UUID | None = Field(
        default=None, foreign_key="recurrentevent.id", nullable=True, ondelete="CASCADE"
    )


class Homework(Base, HomeworkBase, table=True):
    teacher: User = Relationship(
        back_populates="homeworks_as_teacher", sa_relationship_kwargs={"foreign_keys": "Homework.teacher_id"}
    )
    student: User = Relationship(
        back_populates="homeworks_as_student", sa_relationship_kwargs={"foreign_keys": "Homework.student_id"}
    )
    lesson: Event | None = Relationship(
        back_populates="homeworks", sa_relationship_kwargs={"foreign_keys": "Homework.lesson_id"}
    )
    recurrent_lesson: RecurrentEvent | None = Relationship(
        back_populates="homeworks", sa_relationship_kwargs={"foreign_keys": "Homework.recurrent_lesson_id"}
    )
    documents: list[Document] = Relationship(back_populates="homework", cascade_delete=True)

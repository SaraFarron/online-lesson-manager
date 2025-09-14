from random import randint

import pytest
from freezegun import freeze_time
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import Base, Executor, User

# Configure a test database (SQLite in-memory for simplicity)
TEST_DATABASE_URL = "sqlite:///:memory:"

@pytest.fixture(scope="session")
def frozen_time():
    with freeze_time("2025-09-14 09:00:00") as frozen:
        yield frozen


@pytest.fixture(scope="session")
def test_db(frozen_time):  # noqa: ANN001, ARG001
    engine = create_engine(TEST_DATABASE_URL)
    Base.metadata.create_all(engine)
    test_session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    yield test_session_local
    Base.metadata.drop_all(engine)


@pytest.fixture
def db(test_db):  # noqa: ANN001
    session = test_db()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def test_user(db: Session) -> User:
    exec_id = randint(10000, 99999)
    executor = Executor(code=str(exec_id), telegram_id=exec_id)
    db.add(executor)
    db.commit()

    user = User(
        telegram_id=randint(10000, 99999),
        username="test_user",
        full_name="Test User",
        role=User.Roles.TEACHER,
        executor_id=executor.id,
    )
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def test_teacher(db: Session) -> User:
    exec_id = randint(10000, 99999)
    executor = Executor(code=str(exec_id), telegram_id=exec_id)
    db.add(executor)
    db.commit()

    teacher = User(
        telegram_id=randint(10000, 99999),
        username="test_teacher",
        full_name="Test Teacher",
        role=User.Roles.TEACHER,
        executor_id=executor.id,
    )
    db.add(teacher)
    db.commit()
    return teacher


@pytest.fixture
def test_student(db: Session) -> User:
    exec_id = randint(10000, 99999)
    executor = Executor(code=str(exec_id), telegram_id=exec_id)
    db.add(executor)
    db.commit()

    student = User(
        telegram_id=randint(10000, 99999),
        username="test_student",
        full_name="Test Student",
        role=User.Roles.STUDENT,
        executor_id=executor.id,
    )
    db.add(student)
    db.commit()
    return student

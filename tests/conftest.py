import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import Base, Executor, User

# Configure a test database (SQLite in-memory for simplicity)
TEST_DATABASE_URL = "sqlite:///:memory:"

@pytest.fixture(scope="session")
def test_db():
    engine = create_engine(TEST_DATABASE_URL)
    Base.metadata.create_all(engine)
    test_session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    yield test_session_local
    Base.metadata.drop_all(engine)

@pytest.fixture
def db_session(test_db):
    session = test_db()
    try:
        yield session
    finally:
        session.close()

@pytest.fixture
def test_user(db_session: Session) -> User:
    executor = Executor(code="test_executor", telegram_id=12345)
    db_session.add(executor)
    db_session.commit()

    user = User(
        telegram_id=67890,
        username="test_user",
        full_name="Test User",
        role=User.Roles.TEACHER,
        executor_id=executor.id,
    )
    db_session.add(user)
    db_session.commit()
    return user

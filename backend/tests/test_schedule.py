"""Tests for schedule endpoints."""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event, RecurrentEvent, User, UserToken


@pytest.fixture
async def test_user(db_session: AsyncSession) -> User:
    """Create a test user (teacher) with default working hours."""
    user = User(
        telegram_id=123456789,
        username="test_teacher",
        full_name="Test Teacher",
        role=User.Roles.TEACHER,
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest.fixture
async def test_student(db_session: AsyncSession, test_user: User) -> User:
    """Create a test student linked to the test teacher."""
    student = User(
        telegram_id=987654321,
        username="test_student",
        full_name="Test Student",
        role=User.Roles.STUDENT,
        teacher_id=test_user.id,
        is_active=True,
    )
    db_session.add(student)
    await db_session.flush()
    return student


@pytest.fixture
async def auth_token(db_session: AsyncSession, test_user: User) -> str:
    """Create an auth token for the test user."""
    token = UserToken(
        user_id=test_user.id,
        token="test_token_123",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    db_session.add(token)
    await db_session.flush()
    return token.token


@pytest.fixture
async def student_auth_token(db_session: AsyncSession, test_student: User) -> str:
    """Create an auth token for the test student."""
    token = UserToken(
        user_id=test_student.id,
        token="test_student_token_123",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    db_session.add(token)
    await db_session.flush()
    return token.token


class TestFreeSlotsDay:
    """Tests for GET /api/v1/schedule/free-slots/day endpoint."""

    @pytest.mark.asyncio
    async def test_no_events_whole_day_free(
        self, client: AsyncClient, auth_token: str
    ):
        """When there are no events, the whole day should be free (9:00-17:00 UTC)."""
        # Use a future date to ensure free slots are returned
        future_date = (datetime.now(UTC) + timedelta(days=7)).date()

        response = await client.get(
            "/api/v1/schedule/free-slots/day",
            params={"day": future_date.isoformat()},
            headers={"Authorization": f"Bearer {auth_token}"},
        )

        assert response.status_code == 200
        data = response.json()["data"]

        # Should have one continuous free slot from 9:00 to 17:00 UTC
        assert len(data) == 1
        assert data[0]["start"] == "09:00:00"
        assert data[0]["end"] == "17:00:00"

    @pytest.mark.asyncio
    async def test_event_occupies_slot(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        test_user: User,
        test_student: User,
        auth_token: str,
    ):
        """When there is an event, free slots should exclude that time."""
        future_date = (datetime.now(UTC) + timedelta(days=7)).date()

        # Create an event from 10:00 to 11:00 UTC
        event = Event(
            title="Test Lesson",
            start=datetime(future_date.year, future_date.month, future_date.day, 10, 0, tzinfo=UTC),
            end=datetime(future_date.year, future_date.month, future_date.day, 11, 0, tzinfo=UTC),
            teacher_id=test_user.id,
            student_id=test_student.id,
        )
        db_session.add(event)
        await db_session.flush()

        response = await client.get(
            "/api/v1/schedule/free-slots/day",
            params={"day": future_date.isoformat()},
            headers={"Authorization": f"Bearer {auth_token}"},
        )

        assert response.status_code == 200
        data = response.json()["data"]

        # Should have two free slots: 9:00-10:00 and 11:00-17:00
        assert len(data) == 2
        assert data[0]["start"] == "09:00:00"
        assert data[0]["end"] == "10:00:00"
        assert data[1]["start"] == "11:00:00"
        assert data[1]["end"] == "17:00:00"

    @pytest.mark.asyncio
    async def test_multiple_events_occupy_slots(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        test_user: User,
        test_student: User,
        auth_token: str,
    ):
        """Multiple events should create multiple free slot gaps."""
        future_date = (datetime.now(UTC) + timedelta(days=7)).date()

        # Create events at 10:00-11:00 and 14:00-15:00 UTC
        event1 = Event(
            title="Morning Lesson",
            start=datetime(future_date.year, future_date.month, future_date.day, 10, 0, tzinfo=UTC),
            end=datetime(future_date.year, future_date.month, future_date.day, 11, 0, tzinfo=UTC),
            teacher_id=test_user.id,
            student_id=test_student.id,
        )
        event2 = Event(
            title="Afternoon Lesson",
            start=datetime(future_date.year, future_date.month, future_date.day, 14, 0, tzinfo=UTC),
            end=datetime(future_date.year, future_date.month, future_date.day, 15, 0, tzinfo=UTC),
            teacher_id=test_user.id,
            student_id=test_student.id,
        )
        db_session.add_all([event1, event2])
        await db_session.flush()

        response = await client.get(
            "/api/v1/schedule/free-slots/day",
            params={"day": future_date.isoformat()},
            headers={"Authorization": f"Bearer {auth_token}"},
        )

        assert response.status_code == 200
        data = response.json()["data"]

        # Should have three free slots
        assert len(data) == 3
        assert data[0]["start"] == "09:00:00"
        assert data[0]["end"] == "10:00:00"
        assert data[1]["start"] == "11:00:00"
        assert data[1]["end"] == "14:00:00"
        assert data[2]["start"] == "15:00:00"
        assert data[2]["end"] == "17:00:00"


class TestFreeSlotsRange:
    """Tests for GET /api/v1/schedule/free-slots/range endpoint."""

    @pytest.mark.asyncio
    async def test_no_events_range_all_days_free(
        self, client: AsyncClient, auth_token: str
    ):
        """When there are no events, all days in range should be fully free."""
        start_date = (datetime.now(UTC) + timedelta(days=7)).date()
        end_date = (datetime.now(UTC) + timedelta(days=9)).date()

        response = await client.get(
            "/api/v1/schedule/free-slots/range",
            params={
                "start_day": start_date.isoformat(),
                "end_day": end_date.isoformat(),
            },
            headers={"Authorization": f"Bearer {auth_token}"},
        )

        assert response.status_code == 200
        data = response.json()["data"]

        # Should have 3 days, each with one free slot 9:00-17:00
        assert len(data) == 3
        for day_str in data:
            assert len(data[day_str]) == 1
            assert data[day_str][0]["start"] == "09:00:00"
            assert data[day_str][0]["end"] == "17:00:00"

    @pytest.mark.asyncio
    async def test_recurring_event_occupies_weekly_slots(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        test_user: User,
        test_student: User,
        auth_token: str,
    ):
        """Recurring events should occupy slots on the same weekday each week."""
        # Start from next Monday
        today = datetime.now(UTC).date()
        days_until_monday = (7 - today.weekday()) % 7
        if days_until_monday == 0:
            days_until_monday = 7
        next_monday = today + timedelta(days=days_until_monday)

        # Create a recurring event on Monday at 10:00-11:00 UTC (repeats weekly)
        recurrent_event = RecurrentEvent(
            title="Weekly Lesson",
            start=datetime(next_monday.year, next_monday.month, next_monday.day, 10, 0, tzinfo=UTC),
            end=datetime(next_monday.year, next_monday.month, next_monday.day, 11, 0, tzinfo=UTC),
            interval_days=7,
            interval_end=None,
            teacher_id=test_user.id,
            student_id=test_student.id,
        )
        db_session.add(recurrent_event)
        await db_session.flush()

        # Query for two weeks (14 days) starting from next Monday
        start_date = next_monday
        end_date = next_monday + timedelta(days=13)

        response = await client.get(
            "/api/v1/schedule/free-slots/range",
            params={
                "start_day": start_date.isoformat(),
                "end_day": end_date.isoformat(),
            },
            headers={"Authorization": f"Bearer {auth_token}"},
        )

        assert response.status_code == 200
        data = response.json()["data"]

        # Check both Mondays have the event slot occupied
        first_monday = next_monday.isoformat()
        second_monday = (next_monday + timedelta(days=7)).isoformat()

        # First Monday should have 2 free slots (9:00-10:00 and 11:00-17:00)
        assert len(data[first_monday]) == 2
        assert data[first_monday][0]["start"] == "09:00:00"
        assert data[first_monday][0]["end"] == "10:00:00"
        assert data[first_monday][1]["start"] == "11:00:00"
        assert data[first_monday][1]["end"] == "17:00:00"

        # Second Monday should also have 2 free slots (recurring event)
        assert len(data[second_monday]) == 2
        assert data[second_monday][0]["start"] == "09:00:00"
        assert data[second_monday][0]["end"] == "10:00:00"
        assert data[second_monday][1]["start"] == "11:00:00"
        assert data[second_monday][1]["end"] == "17:00:00"

        # Tuesday should be fully free
        first_tuesday = (next_monday + timedelta(days=1)).isoformat()
        assert len(data[first_tuesday]) == 1
        assert data[first_tuesday][0]["start"] == "09:00:00"
        assert data[first_tuesday][0]["end"] == "17:00:00"


class TestScheduleDay:
    """Tests for GET /api/v1/schedule/day endpoint."""

    @pytest.mark.asyncio
    async def test_no_events_empty_schedule(
        self, client: AsyncClient, auth_token: str
    ):
        """When there are no events, schedule should be empty."""
        future_date = (datetime.now(UTC) + timedelta(days=7)).date()

        response = await client.get(
            "/api/v1/schedule/day",
            params={"day": future_date.isoformat()},
            headers={"Authorization": f"Bearer {auth_token}"},
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data) == 0

    @pytest.mark.asyncio
    async def test_event_appears_in_schedule(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        test_user: User,
        test_student: User,
        auth_token: str,
    ):
        """Events should appear in the schedule for their day."""
        future_date = (datetime.now(UTC) + timedelta(days=7)).date()

        # Create an event at 10:00-11:00 UTC
        event = Event(
            title="Test Lesson",
            start=datetime(future_date.year, future_date.month, future_date.day, 10, 0, tzinfo=UTC),
            end=datetime(future_date.year, future_date.month, future_date.day, 11, 0, tzinfo=UTC),
            teacher_id=test_user.id,
            student_id=test_student.id,
        )
        db_session.add(event)
        await db_session.flush()

        response = await client.get(
            "/api/v1/schedule/day",
            params={"day": future_date.isoformat()},
            headers={"Authorization": f"Bearer {auth_token}"},
        )

        assert response.status_code == 200
        data = response.json()["data"]

        assert len(data) == 1
        assert data[0]["title"] == "Test Lesson"
        # Check that start contains the date and time
        assert data[0]["start"].startswith(future_date.isoformat())
        assert "10:00:00" in data[0]["start"]
        assert data[0]["duration"] == 60
        assert data[0]["isRecurring"] is False

    @pytest.mark.asyncio
    async def test_recurring_event_appears_on_correct_weekday(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        test_user: User,
        test_student: User,
        auth_token: str,
    ):
        """Recurring events should appear on the same weekday each week."""
        # Start from next Monday
        today = datetime.now(UTC).date()
        days_until_monday = (7 - today.weekday()) % 7
        if days_until_monday == 0:
            days_until_monday = 7
        next_monday = today + timedelta(days=days_until_monday)

        # Create a recurring event on Monday
        recurrent_event = RecurrentEvent(
            title="Weekly Lesson",
            start=datetime(next_monday.year, next_monday.month, next_monday.day, 10, 0, tzinfo=UTC),
            end=datetime(next_monday.year, next_monday.month, next_monday.day, 11, 0, tzinfo=UTC),
            interval_days=7,
            interval_end=None,
            teacher_id=test_user.id,
            student_id=test_student.id,
        )
        db_session.add(recurrent_event)
        await db_session.flush()

        # Check the first Monday
        response = await client.get(
            "/api/v1/schedule/day",
            params={"day": next_monday.isoformat()},
            headers={"Authorization": f"Bearer {auth_token}"},
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data) == 1
        assert data[0]["title"] == "Weekly Lesson"
        assert data[0]["isRecurring"] is True

        # Check the following Monday (one week later)
        second_monday = next_monday + timedelta(days=7)
        response = await client.get(
            "/api/v1/schedule/day",
            params={"day": second_monday.isoformat()},
            headers={"Authorization": f"Bearer {auth_token}"},
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data) == 1
        assert data[0]["title"] == "Weekly Lesson"
        assert data[0]["isRecurring"] is True

        # Check Tuesday (should be empty)
        tuesday = next_monday + timedelta(days=1)
        response = await client.get(
            "/api/v1/schedule/day",
            params={"day": tuesday.isoformat()},
            headers={"Authorization": f"Bearer {auth_token}"},
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data) == 0


class TestScheduleRange:
    """Tests for GET /api/v1/schedule/range endpoint."""

    @pytest.mark.asyncio
    async def test_no_events_empty_schedule_range(
        self, client: AsyncClient, auth_token: str
    ):
        """When there are no events, all days should have empty schedules."""
        start_date = (datetime.now(UTC) + timedelta(days=7)).date()
        end_date = (datetime.now(UTC) + timedelta(days=9)).date()

        response = await client.get(
            "/api/v1/schedule/range",
            params={
                "start_day": start_date.isoformat(),
                "end_day": end_date.isoformat(),
            },
            headers={"Authorization": f"Bearer {auth_token}"},
        )

        assert response.status_code == 200
        data = response.json()["data"]

        # Should have 3 days, all empty
        assert len(data) == 3
        for day_str in data:
            assert len(data[day_str]) == 0

    @pytest.mark.asyncio
    async def test_events_appear_on_correct_days(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        test_user: User,
        test_student: User,
        auth_token: str,
    ):
        """Events should appear only on their scheduled days."""
        start_date = (datetime.now(UTC) + timedelta(days=7)).date()
        end_date = (datetime.now(UTC) + timedelta(days=9)).date()
        middle_date = start_date + timedelta(days=1)

        # Create an event on the middle day
        event = Event(
            title="Middle Day Lesson",
            start=datetime(middle_date.year, middle_date.month, middle_date.day, 14, 0, tzinfo=UTC),
            end=datetime(middle_date.year, middle_date.month, middle_date.day, 15, 0, tzinfo=UTC),
            teacher_id=test_user.id,
            student_id=test_student.id,
        )
        db_session.add(event)
        await db_session.flush()

        response = await client.get(
            "/api/v1/schedule/range",
            params={
                "start_day": start_date.isoformat(),
                "end_day": end_date.isoformat(),
            },
            headers={"Authorization": f"Bearer {auth_token}"},
        )

        assert response.status_code == 200
        data = response.json()["data"]

        # First day should be empty
        assert len(data[start_date.isoformat()]) == 0

        # Middle day should have the event
        assert len(data[middle_date.isoformat()]) == 1
        assert data[middle_date.isoformat()][0]["title"] == "Middle Day Lesson"

        # Last day should be empty
        assert len(data[end_date.isoformat()]) == 0

    @pytest.mark.asyncio
    async def test_recurring_events_appear_weekly_in_range(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        test_user: User,
        test_student: User,
        auth_token: str,
    ):
        """Recurring events should appear weekly throughout the range."""
        # Start from next Monday
        today = datetime.now(UTC).date()
        days_until_monday = (7 - today.weekday()) % 7
        if days_until_monday == 0:
            days_until_monday = 7
        next_monday = today + timedelta(days=days_until_monday)

        # Create a recurring event on Monday
        recurrent_event = RecurrentEvent(
            title="Weekly Lesson",
            start=datetime(next_monday.year, next_monday.month, next_monday.day, 10, 0, tzinfo=UTC),
            end=datetime(next_monday.year, next_monday.month, next_monday.day, 11, 0, tzinfo=UTC),
            interval_days=7,
            interval_end=None,
            teacher_id=test_user.id,
            student_id=test_student.id,
        )
        db_session.add(recurrent_event)
        await db_session.flush()

        # Query for three weeks
        start_date = next_monday
        end_date = next_monday + timedelta(days=20)

        response = await client.get(
            "/api/v1/schedule/range",
            params={
                "start_day": start_date.isoformat(),
                "end_day": end_date.isoformat(),
            },
            headers={"Authorization": f"Bearer {auth_token}"},
        )

        assert response.status_code == 200
        data = response.json()["data"]

        # Check all three Mondays have the recurring event
        for week in range(3):
            monday = (next_monday + timedelta(days=7 * week)).isoformat()
            assert len(data[monday]) == 1
            assert data[monday][0]["title"] == "Weekly Lesson"
            assert data[monday][0]["isRecurring"] is True


class TestUTCTimeHandling:
    """Tests to verify all input/output times are in UTC."""

    @pytest.mark.asyncio
    async def test_event_times_returned_in_utc(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        test_user: User,
        test_student: User,
        auth_token: str,
    ):
        """Event times in response should be in UTC format."""
        future_date = (datetime.now(UTC) + timedelta(days=7)).date()

        # Create event with explicit UTC time
        event = Event(
            title="UTC Test Lesson",
            start=datetime(future_date.year, future_date.month, future_date.day, 10, 30, tzinfo=UTC),
            end=datetime(future_date.year, future_date.month, future_date.day, 11, 30, tzinfo=UTC),
            teacher_id=test_user.id,
            student_id=test_student.id,
        )
        db_session.add(event)
        await db_session.flush()

        response = await client.get(
            "/api/v1/schedule/day",
            params={"day": future_date.isoformat()},
            headers={"Authorization": f"Bearer {auth_token}"},
        )

        assert response.status_code == 200
        data = response.json()["data"]

        # Verify time is returned correctly (10:30 UTC)
        assert "10:30:00" in data[0]["start"]
        assert data[0]["duration"] == 60

    @pytest.mark.asyncio
    async def test_free_slots_times_in_utc(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        test_user: User,
        test_student: User,
        auth_token: str,
    ):
        """Free slot times should be in UTC format."""
        future_date = (datetime.now(UTC) + timedelta(days=7)).date()

        # Create event at 12:00-13:00 UTC
        event = Event(
            title="Midday Lesson",
            start=datetime(future_date.year, future_date.month, future_date.day, 12, 0, tzinfo=UTC),
            end=datetime(future_date.year, future_date.month, future_date.day, 13, 0, tzinfo=UTC),
            teacher_id=test_user.id,
            student_id=test_student.id,
        )
        db_session.add(event)
        await db_session.flush()

        response = await client.get(
            "/api/v1/schedule/free-slots/day",
            params={"day": future_date.isoformat()},
            headers={"Authorization": f"Bearer {auth_token}"},
        )

        assert response.status_code == 200
        data = response.json()["data"]

        # Verify UTC times: 09:00-12:00 and 13:00-17:00
        assert len(data) == 2
        assert data[0]["start"] == "09:00:00"
        assert data[0]["end"] == "12:00:00"
        assert data[1]["start"] == "13:00:00"
        assert data[1]["end"] == "17:00:00"

    @pytest.mark.asyncio
    async def test_date_input_handled_correctly(
        self,
        client: AsyncClient,
        auth_token: str,
    ):
        """Date input should be handled as ISO format (YYYY-MM-DD)."""
        future_date = (datetime.now(UTC) + timedelta(days=7)).date()

        # Test with ISO date format
        response = await client.get(
            "/api/v1/schedule/day",
            params={"day": future_date.isoformat()},
            headers={"Authorization": f"Bearer {auth_token}"},
        )

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_range_dates_input_handled_correctly(
        self,
        client: AsyncClient,
        auth_token: str,
    ):
        """Range date inputs should be handled as ISO format."""
        start_date = (datetime.now(UTC) + timedelta(days=7)).date()
        end_date = (datetime.now(UTC) + timedelta(days=14)).date()

        response = await client.get(
            "/api/v1/schedule/range",
            params={
                "start_day": start_date.isoformat(),
                "end_day": end_date.isoformat(),
            },
            headers={"Authorization": f"Bearer {auth_token}"},
        )

        assert response.status_code == 200
        data = response.json()["data"]

        # Verify all dates in response are in ISO format
        for day_str in data.keys():
            # Should be parseable as ISO date
            parsed_date = datetime.strptime(day_str, "%Y-%m-%d").date()
            assert start_date <= parsed_date <= end_date

# Online Lesson Manager - Backend Development Guide

## Architecture Overview

This is a **FastAPI async backend** using a strict 4-layer architecture with PostgreSQL and SQLAlchemy:

```
API (routers/endpoints) → Services (business logic) → Repositories (data access) → Models (SQLAlchemy)
```

**Critical rule**: Each layer only talks to the layer below. Services orchestrate repositories, never write SQL directly.

## Project Structure

- `app/api/v1/endpoints/` - FastAPI routers (request validation, responses)
- `app/services/` - Business logic, orchestration (e.g., `EventService`)
- `app/repositories/` - Database queries (inherit from `BaseRepository[ModelType]`)
- `app/models/` - SQLAlchemy ORM models
- `app/schemas/` - Pydantic models for request/response
- `app/core/` - Config (`settings`), middleware, exceptions
- `app/db/` - Database session management
- `alembic/versions/` - Database migrations (use Alembic, not direct SQL)

## Database & Migrations

**Always use async SQLAlchemy** (`AsyncSession`, `asyncpg` driver).

Database URL pattern: `postgresql+asyncpg://user:password@host:port/dbname`

**Creating migrations:**
```bash
# Auto-generate migration from model changes
alembic revision --autogenerate -m "description"

# Run migrations
docker compose run --rm migrate
# or locally: alembic upgrade head
```

**Timezone handling**: All datetime fields use `DateTime(timezone=True)` and are stored/handled in UTC only. Validate in schemas with `validate_utc_only` validator (see [app/schemas/events.py](app/schemas/events.py#L43-L53)).

## Key Patterns

### 1. Repository Pattern
Repositories inherit from `BaseRepository[ModelType]` which provides CRUD operations:
```python
class EventRepository(BaseRepository[Event]):
    def __init__(self, session: AsyncSession):
        super().__init__(Event, session)
    
    # Add custom queries beyond base CRUD
    async def get_by_user(self, user: User) -> list[Event]:
        # Custom SQLAlchemy query here
```

### 2. Service Layer
Services compose multiple repositories and contain business logic:
```python
class EventService:
    def __init__(self, session: AsyncSession):
        self.repository = EventRepository(session)
        self.recurrent_repo = RecurrentEventRepository(session)
        # Initialize all needed repositories
```

Services receive `AsyncSession` and create repositories. **Never pass repositories as dependencies.**

### 3. Dependency Injection
Use type aliases for cleaner endpoint signatures:
```python
DatabaseSession = Annotated[AsyncSession, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]
ServiceKey = Annotated[str, Depends(verify_service_key)]

@router.get("/schedule")
async def get_schedule(db: DatabaseSession, user: CurrentUser):
    service = EventService(db)
    return await service.get_schedule(user, date.today())
```

### 4. Schema Conversion
Schemas have `.to_dict(user)` methods that prepare data for model creation. They handle role-based logic (e.g., determining teacher_id based on user role). See [app/schemas/events.py](app/schemas/events.py#L58-L70).

### 5. Odd/Even ID Sequences
Events use **odd IDs** (1,3,5...), RecurrentEvents use **even IDs** (2,4,6...) via custom Postgres sequences. This allows distinguishing event types without joins. See [app/models/events.py](app/models/events.py#L10-L16).

## Security

**Service-to-service auth**: Internal endpoints (for Telegram bot) require `X-Service-Key` header validated via `verify_service_key` dependency. Use `ServiceKey` type alias.

**User auth**: Uses Bearer token authentication. `get_current_user` dependency validates token and loads user with relationships.

## Development Workflow

**Run locally with Docker:**
```bash
docker compose up --build        # Start API + PostgreSQL
docker compose run --rm migrate  # Run migrations
```

**Tests:**
```bash
poetry run pytest                # Requires local PostgreSQL test DB
# Or: docker compose run api pytest
```

Tests use `conftest.py` fixtures that create/tear down test database and override `get_db` dependency.

**Code quality:**
```bash
poetry run ruff check .          # Linting (configured in pyproject.toml)
```

## Configuration

Settings in `app/core/config.py` using Pydantic Settings. Load from `.env` file:
- `DATABASE_URL` - Required
- `SERVICE_KEY` - Required for internal API auth
- `APP_ENV` - development/staging/production
- Date/time formats customizable via settings

Access via `from app.core.config import settings`

## Common Tasks

**Adding a new endpoint:**
1. Create schema in `app/schemas/` (inherit from `BaseModel`)
2. Add repository method if custom query needed
3. Add service method for business logic
4. Create endpoint in `app/api/v1/endpoints/`, inject `DatabaseSession`
5. Register router in `app/api/v1/router.py`

**Adding a new model:**
1. Create model in `app/models/` inheriting from `Base`
2. Run `alembic revision --autogenerate -m "add_table"`
3. Review generated migration, run `alembic upgrade head`
4. Create corresponding repository and schema

## Integration Context

This backend is designed for **two-server architecture**:
- Server 1: Frontend + Backend (FastAPI) + PostgreSQL
- Server 2: Telegram bot (separate service, uses internal API endpoints)

See [ARCHITECTURE.md](ARCHITECTURE.md) for full system design including Circuit Breaker pattern, notification system, and health checks (partially implemented).

## API Documentation

When running, visit:
- Swagger UI: `http://localhost:8000/api/v1/docs`
- ReDoc: `http://localhost:8000/api/v1/redoc`

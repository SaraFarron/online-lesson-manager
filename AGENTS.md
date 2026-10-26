# Online Lesson Manager — Agent Instructions

Scheduling backend for online tutors (teachers/students), with a Telegram
onboarding bot and a planned HTMX frontend as separate clients. Full product spec
(roles, scheduling rules, notifications) lives in [README.md](./README.md)
(written in Russian) — read it before implementing scheduling/notification
features.

## Running, linting, migrations

- Package manager: **uv** (`uv.lock` present). Install deps: `uv sync`.
- Run dev server: `fastapi run backend/main.py` (uses SQLite `dev.db` via
  `DATABASE_URL` in `.env`). Full stack incl. Postgres: `docker compose up`.
- Run the separate bot: `uv run python -m telegram`, or opt in with
  `docker compose --profile bot up --build`. Configure `BOT_TOKEN`,
  `BOT_BACKEND_URL`, and `BOT_SERVICE_CREDENTIAL` in ignored `.env`; the service
  credential must match backend `TELEGRAM_SERVICE_CREDENTIAL`. Run one poller
  per token. See [README.md](./README.md) for local/remote deployment.
- Apply migrations: `alembic upgrade head` (runs automatically in Docker
  entrypoint). New migration file names follow `YYYY-MM-DD_slug.py`
  (configured in `alembic.ini` via `file_template`).
- Lint/format/typecheck before considering a change done:
  ```shell
  ruff check --fix backend
  ruff format backend
  mypy backend   # strict mode, alembic/ excluded
  ```
- Existing tests use `httpx.AsyncClient` / `ASGITransport`, an isolated real
  SQLite database, and dependency overrides in `tests/conftest.py`.
  Run the relevant smoke test with `uv run pytest -q tests/test_smoke.py`.
  Bot tests feed simulated aiogram updates through a recording Bot API
  transport and the real backend; run `uv run pytest -q tests` for all checks.
  Validate bot changes with `uv run ruff check telegram tests`,
  `uv run ruff format --check telegram tests`, and `uv run mypy telegram`.

For writing release plans, decomposing a README.md feature into small tasks,
or drafting a task description for an AI agent or human, use the
[plan-feature skill](.github/skills/plan-feature/SKILL.md).
Store new plan and task Markdown files in [docs/plans/](docs/plans/), not in
the repository root. Existing plans have been reconciled and moved there;
update relative links and repository references whenever moving plans. Keep
planning templates and planning workflow instructions in
[.github/skills/plan-feature/](.github/skills/plan-feature/); completed plans
remain in `docs/plans/` as project documentation.

## Architecture

- `backend/main.py` wires routers under `/api/v1/...` (auth, lessons,
  recurrent-lessons, breaks, recurrent-breaks, vacations) plus `pages`
  (server-rendered HTML via Jinja2) and an `htmx` router for fragments.
- Domain packages follow the structure in the Project Structure section
  below. **Events are the core abstraction**: `backend/events/` defines
  `Event`/`RecurrentEvent`/`RecurrentCancels` models and composable
  `EventValidator` classes (time overlap, user existence, teacher conflict
  checks). `lessons/`, `breaks/`, and `vacations/` each reuse
  `events` validators/schemas and specialize `event_type`
  (`EventType` enum: `LESSON`, `BREAK`, `VACATION`) — follow this
  compose-don't-duplicate pattern for any new event-like domain.
- Recurring events (`RecurrentEvent`) have an `interval`/`interval_end`;
  individual skipped occurrences are stored as rows in `RecurrentCancels`
  rather than mutating the series.
- Auth: roles are `ADMIN` / `TEACHER` / `STUDENT` (StrEnum on `User.role`);
  students link to a teacher via `User.teacher_id`. JWT via PyJWT,
  passwords via `pwdlib[argon2,bcrypt]`.
  Telegram identity lives directly on `User` (`telegram_user_id`, unique
  64-bit; private chat ID is routing only). Telegram-only users have no
  email/password and cannot use password login or add password credentials.
  `backend/telegram/` provides trusted session/registration/invitation routes
  using a dedicated `TELEGRAM_SERVICE_CREDENTIAL` / `X-Bot-Service-Key`.
  Registration owns one transaction; `auth.service.add_*` helpers flush only.
  Profile routes require owner or superuser; profile PUT is a partial update.
- Top-level `telegram/` is a separate aiogram 3 / aiogram-dialog 2 client,
  not the backend adapter package. It uses typed async HTTP calls, memory-only
  drafts/JWT cache, a bounded circuit breaker, and per-user update isolation.
  Normal inline and text-input transitions edit the active screen. Completed
  users recover through `/start` after restart; incomplete drafts restart.
  Never import backend models/settings/services into the bot or start polling
  inside FastAPI.
- **Stub/unimplemented packages** — don't assume behavior exists just
  because the package exists: `backend/homework/` has schema metadata only,
  with no service/router yet; `backend/schedule/router.py` is empty.
  `backend/notifications/` has only an ORM model, no router/service yet.
- Config is split per-domain (`BaseSettings` subclasses), e.g.
  `backend/config.py` (`Settings`, no prefix) and `backend/auth/config.py`
  (`AuthConfig`, `env_prefix="AUTH_"`). Add new env vars to the owning
  domain's config, not the global one.

## Feature Status (vs product spec)

[README.md](./README.md) (Russian) is the full product spec. This table maps
it to what's actually implemented, so planning/coding tasks don't redo
finished work or assume unbuilt features exist. Keep it updated when you ship
a README-described feature.

| README section | Status | Notes |
|---|---|---|
| Teacher/student registration (`/auth/register`, `/auth/login`) | **Done** | Password auth retained; trusted Telegram API and private-chat `/start` teacher/student wizards implemented. Teacher invitations use short `t_[A-Z0-9]{6}` codes in `/start` payloads; existing codes can be replaced with the explicit rotation command. No account merging/password addition for Telegram-only users. |
| Teacher settings (work hours, lesson length, break, max/day) | **Done** | Validated work hours, JSON ISO weekdays, lesson length, break, max/day and max consecutive preferences are persisted with README defaults and editable in Telegram Settings. Workdays/hours, lesson duration, daily/consecutive limits, and snapshotted automatic post-lesson breaks are enforced. |
| Student settings (notifications) | **Partial** | Validated digest minutes-of-local-day, lesson reminder minutes (1–600), homework days; null disables. Safe partial updates and Telegram Settings editing are implemented. Notification delivery is not implemented. |
| Lessons / breaks / vacations CRUD (one-off + recurring) | **Done** | Shared UTC normalization, recurring occurrence/cancellation lifecycle, teacher-scoped conflict validation, vacation visibility, confirmed manual-break displacement, teacher-local workday/hour checks, lesson duration defaults/authorization, daily/consecutive limits, database-backed per-teacher mutation serialization, and derived automatic post-lesson reservations are implemented. Lesson snapshots default to zero for existing rows; preference changes apply only to future writes. |
| Schedule viewing (day/week, timeline, teacher sees student names) | **Partial** | Authenticated viewer-local day/week endpoints and live Telegram Today/This week screens are implemented, including role-scoped results, previous/next-week navigation, refresh, DST-aware rendering, and safe pagination. HTMX/arbitrary-date UI and offline cache are not implemented. |
| Notifications (morning digest, before-lesson, broadcast, homework) | **Not started** | `backend/notifications/models.py` has only an ORM model (`Notification`); no service, router, or scheduler. |
| Homework (assign/submit/mark done) | **Not started** | Assignment, submission-version, and attachment metadata exist; no service, router, storage, or Telegram homework UI yet. |
| Student profile view for teacher (history, delete w/ confirm) | **Not started** | No action-log table or endpoint. |
| Telegram bot | **Partial** | Separate polling client, teacher/student wizards, invitation/code entry, role homes, editable post-onboarding settings, lesson/event management with automatic-break previews, manual breaks, vacations, retry/recovery and edit-in-place screens. Live schedule viewing includes previous/next-week navigation; homework actions, notification delivery and offline schedule cache are not implemented. |
| HTMX frontend | **Scaffold only** | `templates/` has base/index/login/register pages and one HTMX fragment (`/htmx/server-time`); no real page is wired to the API yet. |

## Scheduling and Telegram handoff

The implemented Telegram scheduling/settings scope and cross-flow evidence are
tracked in [telegram-scheduling-and-settings-ui](docs/plans/telegram-scheduling-and-settings-ui.md).
[Automatic between-lesson breaks](docs/plans/automatic-between-lesson-breaks.md)
are implemented and verified; [homework](docs/plans/homework-backend-and-telegram-ui.md)
is the next follow-on. Notification delivery and homework actions remain
unimplemented.

## Scheduling Foundation Boundaries

- Event and cancellation columns remain naive database timestamps, but their
  stored values are UTC. API input with no offset means UTC; responses use
  offset-bearing UTC values. New recurrences snapshot the teacher's timezone;
  legacy series retain UTC-step behavior. See the scheduling handoff in
  [README.md](./README.md) before changing schedule behavior.
- The internal event range service returns teacher-scoped raw occurrences and
  bulk-applies cancellation rows. `backend/events/visibility.py` separately
  computes vacation-hidden occurrences; callers must apply viewer authorization
  before returning another student's event details.
- Authenticated `/api/v1/schedule/day` and `/api/v1/schedule/week` endpoints
  and live Telegram Today/This week screens are implemented, including
  previous/next-week navigation. The bot reads current data from the backend;
  offline caching and an HTMX/arbitrary-date schedule UI remain unimplemented.
  Apply viewer authorization before shaping or caching results; never return
  teacher-wide raw event details to a student.
- Workday/hour and duration enforcement, daily/consecutive limits, and
  database-backed mutation serialization are implemented. Automatic breaks
  are derived from each lesson's snapshotted duration; existing lessons and
  series default to zero, and preference changes affect future writes only.
  Reservations are read-only, follow parent cancellation and visibility, may
  extend beyond work hours or local midnight, and do not count as lessons.
- Keep break-displacement confirmation and vacation display-only semantics
  stable. Confirmed break creation permanently deletes one-off lessons or
  cancels exact recurring occurrences; deleting a break does not restore them.

## Compatibility Matrix

Versions actually pinned in `pyproject.toml` for this repo (examples in this
file assume these or newer):

| Dependency       | Pinned    | Notes                                                |
|------------------|-----------|------------------------------------------------------|
| Python           | 3.14      | Required for `StrEnum` and `X \| Y` union syntax     |
| FastAPI          | 0.141     | `Annotated[T, Depends(...)]` is the idiomatic form   |
| Pydantic         | 2.4+      | v1 APIs (`json_encoders`, `.dict()`) are removed     |
| pydantic-settings| 2.4       | Lives in a separate package since Pydantic v2        |
| SQLAlchemy       | 2.0       | Use the async API (`AsyncSession`, `async_sessionmaker`) |
| Alembic          | 1.13      | Async-aware migrations                               |
| httpx            | 0.27      | Use `ASGITransport` for in-process tests             |
| PyJWT            | 2.9       | Use this, not the unmaintained `python-jose`         |
| ruff             | 0.6       | Replaces black, isort, autoflake                     |

## Project Structure

Organize by domain, not by file type. One package per bounded context.

```
backend/
├── {domain}/           # e.g., auth/, posts/, aws/
│   ├── router.py       # API endpoints
│   ├── schemas.py      # Pydantic models
│   ├── models.py       # SQLAlchemy ORM models
│   ├── service.py      # Business logic
│   ├── dependencies.py # Route dependencies
│   ├── config.py       # Domain-scoped BaseSettings
│   ├── constants.py    # Constants and error codes
│   ├── exceptions.py   # Domain-specific exceptions
│   └── utils.py        # Helper functions
├── config.py           # Global BaseSettings
├── models.py           # Shared Pydantic / ORM bases
├── exceptions.py       # Global exceptions
├── database.py         # Async engine + session factory
└── main.py             # FastAPI app + lifespan
```

**Cross-domain imports**: always use the explicit module name. Never `from backend.auth import *`.

```python
from backend.auth import constants as auth_constants
from backend.notifications import service as notification_service
from backend.posts.constants import ErrorCode as PostsErrorCode
```

## Async Routes

### Decision rule

| Route does this                        | Use         |
|----------------------------------------|-------------|
| `await`-able non-blocking I/O          | `async def` |
| Blocking I/O (no async client exists)  | `def` (sync, runs in threadpool) |
| Mix of both                            | `async def` + `run_in_threadpool` for the blocking part |
| CPU-bound work (>50 ms compute)        | Offload to a worker process (Celery / RQ / Arq) |

### Do / Don't

```python
# DON'T — blocking call inside async route freezes the entire event loop
@router.get("/bad")
async def bad():
    time.sleep(10)  # blocks every request on this worker
    return {"ok": True}


# DO — sync route lets FastAPI run it in a threadpool
@router.get("/sync-ok")
def sync_ok():
    time.sleep(10)  # blocks one threadpool worker, not the loop
    return {"ok": True}


# DO — async route with awaitable sleep
@router.get("/async-ok")
async def async_ok():
    await asyncio.sleep(10)  # yields control, loop keeps serving requests
    return {"ok": True}


# DO — async route that has to call a sync library
from fastapi.concurrency import run_in_threadpool


@router.get("/wrap")
async def wrap():
    result = await run_in_threadpool(legacy_sync_client.fetch, "id")
    return result
```

### Threadpool caveats
- Default Starlette threadpool size is 40. Saturating it slows every sync route.
- Threads cost more than coroutines. Don't use sync routes "just because."

## Pydantic

### Use built-in validators
```python
from enum import StrEnum
from pydantic import AnyUrl, BaseModel, EmailStr, Field


class MusicBand(StrEnum):
    AEROSMITH = "AEROSMITH"
    QUEEN = "QUEEN"
    ACDC = "AC/DC"


class UserCreate(BaseModel):
    first_name: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    email: EmailStr
    age: int = Field(ge=18)  # required, must be >= 18
    favorite_band: MusicBand | None = None
    website: AnyUrl | None = None
```

> **Don't** write `Field(ge=18, default=None)`. The constraint and the default contradict
> each other. Decide: required (`Field(ge=18)`) or optional (`int | None = Field(default=None, ge=18)`).

### Custom base model — modern serialization

`json_encoders` is deprecated in Pydantic v2. Use `@field_serializer` for per-field rules,
or annotate a custom type with `PlainSerializer`.

```python
from datetime import datetime
from zoneinfo import ZoneInfo
from pydantic import BaseModel, ConfigDict, field_serializer


class CustomModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    @field_serializer("*", when_used="json", check_fields=False)
    def _serialize_datetimes(self, value):
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=ZoneInfo("UTC"))
            return value.strftime("%Y-%m-%dT%H:%M:%S%z")
        return value
```

### Split BaseSettings by domain

`pydantic-settings` is its own package since Pydantic v2.

```python
# backend/auth/config.py
from datetime import timedelta
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AUTH_", env_file=".env", extra="ignore"
    )

    JWT_ALG: str
    JWT_SECRET: str
    JWT_EXP_MINUTES: int = 5
    REFRESH_TOKEN_KEY: str
    REFRESH_TOKEN_EXP: timedelta = timedelta(days=30)
    SECURE_COOKIES: bool = True


auth_settings = AuthConfig()
```

## Dependencies

### Use Annotated, not default-arg `Depends(...)`

`Annotated[T, Depends(...)]` is the idiomatic form since FastAPI 0.95 and avoids
gotchas with default values.

```python
# DO — modern Annotated form
from typing import Annotated
from fastapi import Depends

PostDep = Annotated[dict, Depends(valid_post_id)]


@router.get("/posts/{post_id}")
async def get_post(post: PostDep):
    return post


# Avoid — default-argument form (still works, but legacy)
@router.get("/posts/{post_id}")
async def get_post(post: dict = Depends(valid_post_id)):
    return post
```

### Validate inside dependencies (not just inject)
```python
async def valid_post_id(post_id: UUID4) -> dict:
    post = await service.get_by_id(post_id)
    if not post:
        raise PostNotFound()
    return post
```

### Chain dependencies for reuse
```python
async def valid_owned_post(
    post: Annotated[dict, Depends(valid_post_id)],
    token_data: Annotated[dict, Depends(parse_jwt_data)],
) -> dict:
    if post["creator_id"] != token_data["user_id"]:
        raise UserNotOwner()
    return post
```

### Rules
- Dependencies are **cached per request**. Same `Depends(x)` called 5 times in one request → `x` runs once.
- Prefer `async def` dependencies. Sync deps run in the threadpool — wasted overhead for small CPU-only checks.
- Use **the same path-variable name** across endpoints when you want to share a dependency (e.g. `profile_id` in both `/profiles/{profile_id}` and `/creators/{profile_id}`).

## Authentication — JWT

Use **`PyJWT`**, not `python-jose` (unmaintained).

```python
import jwt  # PyJWT
from jwt.exceptions import InvalidTokenError


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALG])
    except InvalidTokenError as exc:
        raise InvalidCredentials() from exc
```

## Database — SQLAlchemy 2.0 async

Prefer SQLAlchemy 2.0's async API. `encode/databases` is in maintenance mode — don't pick it for new projects.

```python
# backend/database.py
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

engine = create_async_engine(str(settings.DATABASE_URL), pool_pre_ping=True)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncSession:
    async with SessionFactory() as session:
        yield session
```

### Naming conventions
- `lower_case_snake`
- Singular tables: `post`, `user`, `post_like`
- Group with prefix: `payment_account`, `payment_bill`
- `_at` suffix for `datetime`, `_date` suffix for `date`
- Use the same FK column name everywhere it appears (`profile_id`, not `user_id` in some tables and `profile_id` in others)

### Index naming convention
```python
from sqlalchemy import MetaData

POSTGRES_INDEXES_NAMING_CONVENTION = {
    "ix": "%(column_0_label)s_idx",
    "uq": "%(table_name)s_%(column_0_name)s_key",
    "ck": "%(table_name)s_%(constraint_name)s_check",
    "fk": "%(table_name)s_%(column_0_name)s_fkey",
    "pk": "%(table_name)s_pkey",
}
metadata = MetaData(naming_convention=POSTGRES_INDEXES_NAMING_CONVENTION)
```

### SQL-first, Pydantic-second
- Do joins, aggregation, and JSON shaping in SQL — Postgres is faster than CPython at this.
- Hydrate the result into Pydantic only for response validation, not for transformation.

## Background work — BackgroundTasks vs Celery

| Use BackgroundTasks when…                | Use Celery / Arq / RQ when…                |
|------------------------------------------|--------------------------------------------|
| Task is < 1 second                       | Task takes seconds to minutes              |
| Failure can be silently dropped          | You need retries, dead-letter, or visibility|
| Task is in-process (send email, log row) | Task is CPU-heavy or needs a separate pool |
| You don't need scheduling                | You need cron, ETA, or rate limiting       |

```python
from fastapi import BackgroundTasks


@router.post("/signup")
async def signup(data: SignupIn, bg: BackgroundTasks):
    user = await service.create_user(data)
    bg.add_task(send_welcome_email, user.email)  # fire-and-forget, in-process
    return user
```

> BackgroundTasks run **after the response is sent, in the same worker process**. If the
> worker dies, the task is lost. There is no retry. Don't use them for anything you'd
> page on.

## Testing

### Async client from day one
```python
import pytest
from httpx import AsyncClient, ASGITransport

from backend.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_create_post(client: AsyncClient):
    resp = await client.post("/posts", json={"title": "hi"})
    assert resp.status_code == 201
```

> **Don't** use `async_asgi_testclient` — it's unmaintained. The example above (httpx +
> `ASGITransport`) is the supported path.

### Override dependencies in tests
Don't monkeypatch internals. Use FastAPI's built-in `dependency_overrides`.

```python
from backend.auth.dependencies import parse_jwt_data
from backend.main import app


def fake_user():
    return {"user_id": "00000000-0000-0000-0000-000000000001"}


@pytest.fixture(autouse=True)
def _override_auth():
    app.dependency_overrides[parse_jwt_data] = fake_user
    yield
    app.dependency_overrides.clear()
```

## Migrations (Alembic)

- Migrations must be static and reversible.
- Use the async template: `alembic init -t async migrations`
- Descriptive filenames:
  ```ini
  # alembic.ini
  file_template = %%(year)d-%%(month).2d-%%(day).2d_%%(slug)s
  ```
  → `2026-04-14_add_post_content_idx.py`

## API documentation

### Hide docs outside selected envs
```python
from fastapi import FastAPI
from backend.config import settings

SHOW_DOCS_IN = {"local", "staging"}
app_kwargs = {"title": "My API"}
if settings.ENVIRONMENT not in SHOW_DOCS_IN:
    app_kwargs["openapi_url"] = None  # disables /docs and /redoc

app = FastAPI(**app_kwargs)
```

### Document endpoints fully
```python
from fastapi import APIRouter, status

router = APIRouter()


@router.post(
    "/items",
    response_model=ItemResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an item",
    description="Creates an item owned by the authenticated user.",
    tags=["items"],
    responses={
        status.HTTP_400_BAD_REQUEST: {
            "model": ErrorResponse,
            "description": "Validation error",
        },
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": "Slug already exists",
        },
    },
)
async def create_item(payload: ItemCreate) -> ItemResponse: ...
```

## Linting

```shell
ruff check --fix backend
ruff format backend
```

Add to a pre-commit hook or run in CI. Ruff replaces black + isort + autoflake + most of flake8.

---

## Anti-patterns — common AI-agent mistakes

If you're an agent reviewing a diff, check for these. Each is a real failure mode I've
seen agents introduce.

| Anti-pattern | Why it's wrong | Fix |
|---|---|---|
| `requests.get(...)` inside `async def` | Blocks the event loop. `requests` is sync. | Use `httpx.AsyncClient` or `await run_in_threadpool(requests.get, ...)`. |
| `time.sleep` / `open()` / sync DB driver inside `async def` | Same — blocks the loop. | Use the async equivalent (`asyncio.sleep`, `aiofiles`, async driver). |
| `from jose import jwt` | `python-jose` is unmaintained. | `import jwt` (PyJWT). |
| `from async_asgi_testclient import TestClient` | Unmaintained. | `httpx.AsyncClient` + `ASGITransport`. |
| `model_config = ConfigDict(json_encoders={...})` | Deprecated in Pydantic v2. | `@field_serializer` or `Annotated[T, PlainSerializer(...)]`. |
| `Field(ge=18, default=None)` | Constraint contradicts the default. | Pick required or optional, not both. |
| `def get_user(id: int = Depends(...))` (default-arg form) | Legacy; gotchas with default values. | `user: Annotated[User, Depends(...)]`. |
| Catching `Exception` around a route's body | Hides bugs and turns 500s into silent 200s. | Catch the specific exception class; raise `HTTPException` with a meaningful status. |
| `BackgroundTasks` for anything you'd page on | No retry, dies with the worker. | Use Celery / Arq / RQ. |
| Calling a sync ORM session inside `async def` | Blocks the loop, may deadlock the pool. | Use `AsyncSession`. |
| Returning a Pydantic model and *also* setting `response_model=` to that same class | Model gets constructed twice (validate + serialize). | Either return a `dict`/ORM row and let `response_model` validate, or drop `response_model` and trust the return type. |
| Importing across domains via deep paths (`from backend.auth.service.user import ...`) | Tight coupling, hard to refactor. | `from backend.auth import service as auth_service`. |
| Reusing one `BaseSettings` for the whole app | Hard to reason about, every domain reads every var. | One `BaseSettings` per domain. |
| Mocking the database in integration tests | Mock/prod divergence eventually fires in prod. | Use a real DB (testcontainers, ephemeral schema) and `dependency_overrides` for auth/external services. |

## Quick reference

| Scenario                             | Solution                                          |
|--------------------------------------|---------------------------------------------------|
| Non-blocking I/O                     | `async def` route with `await`                    |
| Blocking I/O (no async client)       | `def` route (sync, runs in threadpool)            |
| Sync library inside async route      | `await run_in_threadpool(fn, *args)`              |
| CPU-intensive work                   | Celery / Arq / RQ worker process                  |
| Request validation against DB        | Dependency that loads + validates + returns       |
| Reuse validation across routes       | Chain dependencies                                |
| Inject dependency in modern style    | `Annotated[T, Depends(...)]`                      |
| Per-request dep caching              | Default behavior — same `Depends(x)` runs once    |
| Per-domain config                    | One `BaseSettings` subclass per domain            |
| Custom datetime serialization        | `@field_serializer`                               |
| Fire-and-forget short task           | `BackgroundTasks`                                 |
| Reliable / scheduled / heavy task    | Celery / Arq / RQ                                 |
| JWT decode                           | `PyJWT` (`import jwt`)                            |
| Async DB                             | SQLAlchemy 2.0 async (`AsyncSession`)             |
| HTTP test client                     | `httpx.AsyncClient` + `ASGITransport`             |
| Swap dep in tests                    | `app.dependency_overrides[dep] = fake`            |
| Lint + format                        | `ruff check --fix` + `ruff format`                |
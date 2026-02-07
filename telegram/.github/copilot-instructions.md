# AI Coding Agent Instructions

## Project Overview
Telegram bot for managing online lesson schedules. Async Python using **aiogram 3.x** (Telegram bot framework). 

**⚠️ RECONSTRUCTION IN PROGRESS**: Project is transitioning from local SQLAlchemy/Alembic to a **remote backend model**. All database and migration infrastructure is being removed. The bot now relies entirely on:
- **Backend API** (remote) for persistent data
- **In-memory cache** with TTL (300s default) for performance
- **Circuit breaker pattern** for resilience

Handlers that reference SQL sessions are outdated and will be refactored.

## Architecture

### Component Layers
1. **Routers** (`src/routers/`) - FSM-based handlers split by domain (lessons, schedule, users, common)
2. **Services** (`src/service/`) - Business logic layer; `BackendClient` orchestrates API calls + caching
3. **States** (`src/states/`) - Finite state machine definitions for multi-step user flows
4. **Schemas** (`src/schemas/`) - Pydantic models for validation
5. **Core** (`src/core/`) - Config, logging, middleware, error handling, constants
6. **Messages** (`src/messages/`) - User-facing strings (replies, errors)
7. **Keyboards** (`src/keyboards/`) - Inline/reply keyboard builders

### Data Flow Pattern
```
Router Handler → Service (calls BackendClient)
  → BackendClient: Check cache (TTL) → Fetch backend → Cache result
  → BotCache: In-memory TTLCache (300s TTL, 100 max entries)
  → Circuit Breaker: Protects against backend failures
  → Error Handler (catches & logs, returns user-friendly message)
```

### Key External Dependencies
- **aiogram**: Telegram bot framework with FSM middleware for multi-step conversations
- **BackendClient** ([src/service/backend_client.py](src/service/backend_client.py)): API orchestrator with read-through cache + circuit breaker
- **BotCache** ([src/service/cache.py](src/service/cache.py)): TTLCache wrapper storing `UserCacheData` (slots, events, settings)
- **circuitbreaker**: Prevents cascading failures on backend unavailability
- **cachetools**: TTLCache implementation for efficient expiration
- **pytz**: Moscow timezone used throughout (see `TIMEZONE` in `config.py`)

## Critical Patterns

### FSM State Structure
States define multi-step flows. Example pattern from [src/states/add_lesson.py](src/states/add_lesson.py):
```python
class AddLesson(StatesGroup):
    scene = "add_lesson"  # command/button text
    command = "/" + scene  # /add_lesson
    choose_date = State()
    choose_time = "add_lesson/choose_time/"  # callback_data prefix
```

### Router Pattern (see [src/routers/lessons/add_lesson.py](src/routers/lessons/add_lesson.py))
- Use `@router.message(Command(StateClass.command))` to initiate flows
- Use `AddLessonService(message, state)` to encapsulate logic
- Always check `student_permission(message)` to verify user auth
- Update FSM state via `state.update_data()` and `state.set_state()`

### Error Handling (see [src/core/errors.py](src/core/errors.py))
Raise exceptions with tuple: `raise ValueError(("message", "User-facing text", "Log data"))`
- First element is "message" = send to user; omit or any other value = silent logging

### BackendClient Read-Through Cache Pattern (see [src/service/backend_client.py](src/service/backend_client.py))
**New architecture - NO local database:**
- `get_user_cache_data(telegram_id)`: Check TTL cache → Fetch backend → Cache result
- Methods return `None` on network/circuit breaker failures (not exceptions)
- Always check for `None` return values
- Cache is automatically invalidated via TTL; manual `invalidate_user(telegram_id)` after write operations
- **Cache structure** ([src/service/cache.py](src/service/cache.py)):
  - `free_slots`: dict[date_str → list[Slot]]
  - `schedule`: dict[date_str → list[Event]]
  - `user_settings`: UserSettings object
  - `recurrent_free_slots`: dict[weekday(0-6) → list[Slot]]

## Developer Workflows

### Run Locally
```bash
poetry run python src/main.py  # Requires .env with BOT_TOKEN
```

### Run in Docker
```bash
docker compose up -d --build
```

### Database & Migrations (DEPRECATED)
⚠️ **Do not use** - database infrastructure is being removed:
```bash
# OLD (legacy docs, ignore):
# alembic revision --autogenerate -m 'description'
# alembic upgrade head
```

## Project-Specific Conventions

### Timezone Handling
All times use Moscow timezone (`pytz.timezone("Europe/Moscow")`). Constants: `WORK_START=09:00`, `WORK_END=21:00`, time slots are 15-min increments (`SLOT_SIZE`).

### Configuration
Single `load_config()` call in [src/core/config.py](src/core/config.py) loads `.env`. Bot token required; admin IDs may be deprecated (check before using).

### Testing
Use `freezegun` for time mocking; `pytest` for test runner. Validate imports via [validate_imports.py](validate_imports.py).

### Active vs. Commented Routers
Most routers in [src/routers/__init__.py](src/routers/__init__.py) are commented out (e.g., week_schedule, vacations). Only `add_lesson`, `start`, `help`, `cancel` are active. Uncomment carefully—they may depend on backend endpoints or contain outdated SQL session references.

### Refactoring SQL Sessions to Cache Layer
When refactoring commented-out routers or handlers with SQL sessions:
- Replace `session.query()` patterns with `BackendClient` method calls
- Use `get_user_cache_data()` for read operations; returns `None` on backend unavailability
- After write operations (create/update), call `invalidate_user(telegram_id)` to clear the TTL cache
- Ensure all handlers check for `None` returns from cache lookups

## Key Files Reference
- **Main entry**: [src/main.py](src/main.py)
- **Config & constants**: [src/core/config.py](src/core/config.py)
- **Backend integration**: [src/service/backend_client.py](src/service/backend_client.py)
- **Error handling**: [src/core/errors.py](src/core/errors.py)
- **Router registry**: [src/routers/__init__.py](src/routers/__init__.py)

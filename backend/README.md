# Online Lesson Manager - Backend

A production-ready FastAPI backend with async SQLAlchemy, PostgreSQL, and Docker.

## Architecture

This project uses a **layered architecture with service/repository pattern**:

```
┌─────────────────────────────────────────────────────────┐
│                      API Layer                          │
│  (FastAPI routers, request/response handling, validation)│
├─────────────────────────────────────────────────────────┤
│                    Service Layer                        │
│  (Business logic, orchestration, domain rules)          │
├─────────────────────────────────────────────────────────┤
│                   Repository Layer                      │
│  (Data access, SQL queries, database operations)        │
├─────────────────────────────────────────────────────────┤
│                    Database Layer                       │
│  (SQLAlchemy models, async sessions, migrations)        │
└─────────────────────────────────────────────────────────┘
```

### Why This Architecture?

1. **Separation of Concerns**: Each layer has a single responsibility
2. **Testability**: Easy to mock dependencies at each layer
3. **Flexibility**: Can swap implementations without affecting other layers
4. **Scalability**: Ready for future features (auth, caching, etc.)

## Project Structure

```
backend/
├── alembic/              # Database migrations
│   ├── versions/         # Migration files
│   ├── env.py           # Alembic configuration
│   └── script.py.mako   # Migration template
├── app/
│   ├── api/             # API layer
│   │   ├── deps.py      # Shared dependencies
│   │   └── v1/          # API version 1
│   │       ├── router.py
│   │       └── endpoints/
│   ├── core/            # Core configuration
│   │   └── config.py    # Pydantic settings
│   ├── db/              # Database setup
│   │   ├── base.py      # Declarative base
│   │   └── session.py   # Async session factory
│   ├── models/          # SQLAlchemy models
│   ├── repositories/    # Data access layer
│   ├── schemas/         # Pydantic schemas
│   ├── services/        # Business logic layer
│   └── main.py          # Application factory
├── tests/               # Test suite
├── docker-compose.yml   # Docker services
├── Dockerfile           # Container definition
└── pyproject.toml       # Poetry dependencies
```

## Quick Start

### Prerequisites

- Docker and Docker Compose
- (Optional) Python 3.11+ and Poetry for local development

### Running with Docker

1. **Start all services:**
   ```bash
   docker compose up --build
   ```

2. **The API will be available at:**
   - API: http://localhost:8000
   - Swagger UI: http://localhost:8000/api/v1/docs
   - ReDoc: http://localhost:8000/api/v1/redoc

3. **Run migrations:**
   ```bash
   docker compose run --rm migrate
   ```

### Local Development (without Docker)

1. **Install dependencies:**
   ```bash
   poetry install
   ```

2. **Set up environment variables:**
   ```bash
   cp .env.example .env
   # Edit .env with your database URL
   ```

3. **Start PostgreSQL** (or use Docker):
   ```bash
   docker compose up db -d
   ```

4. **Run migrations:**
   ```bash
   poetry run alembic upgrade head
   ```

5. **Start the development server:**
   ```bash
   poetry run uvicorn app.main:app --reload
   ```

## Database Migrations

### Create a new migration

```bash
# With Docker
docker compose run --rm api alembic revision --autogenerate -m "Description"

# Local
poetry run alembic revision --autogenerate -m "Description"
```

### Apply migrations

```bash
# With Docker
docker compose run --rm migrate

# Or manually
docker compose run --rm api alembic upgrade head

# Local
poetry run alembic upgrade head
```

### Rollback migration

```bash
# Rollback one version
poetry run alembic downgrade -1

# Rollback to specific version
poetry run alembic downgrade <revision_id>
```

## Adding New Features

### 1. Add a New Model

Create `app/models/your_model.py`:

```python
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class YourModel(Base):
    __tablename__ = "your_models"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
```

Update `app/models/__init__.py`:
```python
from app.models.your_model import YourModel
```

Update `alembic/env.py` to import the model, then run migrations.

### 2. Add a New Schema

Create `app/schemas/your_model.py`:

```python
from pydantic import BaseModel


class YourModelCreate(BaseModel):
    name: str


class YourModelResponse(BaseModel):
    id: int
    name: str

    class Config:
        from_attributes = True
```

### 3. Add a New Repository

Create `app/repositories/your_model.py`:

```python
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.your_model import YourModel
from app.repositories.base import BaseRepository


class YourModelRepository(BaseRepository[YourModel]):
    def __init__(self, session: AsyncSession):
        super().__init__(YourModel, session)

    # Add custom queries here
```

### 4. Add a New Service

Create `app/services/your_model.py`:

```python
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.your_model import YourModelRepository
from app.schemas.your_model import YourModelCreate


class YourModelService:
    def __init__(self, session: AsyncSession):
        self.repository = YourModelRepository(session)

    async def create(self, data: YourModelCreate):
        return await self.repository.create(data.model_dump())
```

### 5. Add New Endpoints

Create `app/api/v1/endpoints/your_model.py`:

```python
from fastapi import APIRouter

from app.api.deps import DatabaseSession
from app.schemas.your_model import YourModelCreate, YourModelResponse
from app.services.your_model import YourModelService

router = APIRouter()


@router.post("", response_model=YourModelResponse)
async def create(data: YourModelCreate, db: DatabaseSession):
    service = YourModelService(db)
    return await service.create(data)
```

Register in `app/api/v1/router.py`:

```python
from app.api.v1.endpoints import your_model

api_router.include_router(
    your_model.router,
    prefix="/your-models",
    tags=["your-models"],
)
```

## API Endpoints

### Health Check
- `GET /api/v1/health` - Basic health check
- `GET /api/v1/health/db` - Database connectivity check

### Lessons
- `GET /api/v1/lessons` - List all lessons
- `GET /api/v1/lessons/{id}` - Get a specific lesson
- `GET /api/v1/lessons/search?title=...` - Search lessons
- `POST /api/v1/lessons` - Create a lesson
- `PATCH /api/v1/lessons/{id}` - Update a lesson
- `DELETE /api/v1/lessons/{id}` - Delete a lesson

## Testing

```bash
# Run tests
poetry run pytest

# Run with coverage
poetry run pytest --cov=app
```

## Configuration

All configuration is done via environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string | Required |
| `APP_ENV` | Environment (development/staging/production) | development |
| `DEBUG` | Enable debug mode | false |
| `APP_NAME` | Application name | Online Lesson Manager |
| `API_V1_PREFIX` | API prefix | /api/v1 |

## License

MIT


# Migration shortcut
```sh
docker compose run --rm --user root api alembic revision --autogenerate -m "add_tokens_and_history"
docker compose run --rm api alembic upgrade head
```

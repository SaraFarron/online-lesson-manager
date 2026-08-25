# ── Build stage ───────────────────────────────────────────────────────────────
FROM python:3.14-slim AS builder

WORKDIR /app

# Install uv from the official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy dependency manifests first (layer-cache friendly)
COPY pyproject.toml uv.lock* ./

# Install production dependencies only (no dev extras, no project itself)
RUN uv sync --frozen --no-dev --no-install-project

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Copy the virtual environment from the build stage
COPY --from=builder /app/.venv /app/.venv

# Copy application code and migration files
COPY src/       ./src/
COPY alembic/   ./alembic/
COPY alembic.ini ./

EXPOSE 8000

# Apply pending migrations then start the server
CMD ["sh", "-c", "alembic upgrade head && fastapi run src/main.py --port 8000 --host 0.0.0.0"]

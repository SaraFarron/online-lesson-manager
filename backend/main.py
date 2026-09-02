from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from backend.auth import models as auth_models  # noqa: F401
from backend.auth.router import router as auth_router
from backend.config import settings
from backend.lessons.router import router as lessons_router
from backend.pages.router import htmx_router
from backend.pages.router import router as pages_router

SHOW_DOCS_IN = {"local", "staging"}


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    # ── Startup ──────────────────────────────────────────────────────────────
    yield
    # ── Shutdown ─────────────────────────────────────────────────────────────


app_kwargs: dict = {
    "title": settings.PROJECT_NAME,
    "lifespan": lifespan,
    "docs_url": "/docs" if settings.ENVIRONMENT in SHOW_DOCS_IN else None,
    "redoc_url": "/redoc" if settings.ENVIRONMENT in SHOW_DOCS_IN else None,
    "openapi_url": "/openapi.json" if settings.ENVIRONMENT in SHOW_DOCS_IN else None,
}

app = FastAPI(**app_kwargs)

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(pages_router)
app.include_router(htmx_router)
app.include_router(auth_router, prefix=settings.API_V1_STR)
app.include_router(lessons_router, prefix=settings.API_V1_STR)

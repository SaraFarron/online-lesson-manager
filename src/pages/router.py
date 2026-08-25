from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.config import settings

router = APIRouter(include_in_schema=False)

# HTMX fragment router — mounted at /htmx in main.py
htmx_router = APIRouter(prefix="/htmx", include_in_schema=False)
templates = Jinja2Templates(directory="templates")


def _ctx(_request: Request, **extra: Any) -> dict[str, Any]:
    """Build a base template context shared by every page.

    Note: `request` is passed as a separate first argument to TemplateResponse
    in Starlette 1.x; it must NOT also appear inside this dict.
    """
    return {"app_name": settings.PROJECT_NAME, **extra}


# ── Full pages ────────────────────────────────────────────────────────────────


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html", _ctx(request))


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "login.html", _ctx(request))


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "register.html", _ctx(request))


# ── HTMX fragments (return partial HTML, not full pages) ──────────────────────


@htmx_router.get("/server-time", response_class=HTMLResponse)
async def server_time(request: Request) -> HTMLResponse:  # noqa: ARG001
    """HTMX fragment: returns a small HTML snippet, not a full page."""
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    return HTMLResponse(
        f'<div id="server-time"><strong>{now}</strong> '
        f'<button hx-get="/htmx/server-time" hx-target="#server-time" hx-swap="outerHTML">'
        f"Refresh</button></div>"
    )

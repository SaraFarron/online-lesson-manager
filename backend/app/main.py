import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer

from app.api.v1 import api_router
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.middleware import ResponseWrapperMiddleware
from app.db.session import async_session_factory
from app.services.cleanup import cleanup_expired_tokens

logger = logging.getLogger(__name__)

# Security scheme for Swagger UI
bearer_scheme = HTTPBearer()


async def periodic_cleanup(interval_seconds: int = 3600):
    """Run cleanup task periodically."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            async with async_session_factory() as session:
                deleted = await cleanup_expired_tokens(session)
                if deleted > 0:
                    logger.info(f"Cleaned up {deleted} expired tokens")
        except Exception as e:
            logger.error(f"Error during token cleanup: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for startup and shutdown events.

    Add startup logic before yield, cleanup logic after yield.
    """
    # Startup - start background cleanup task
    cleanup_task = asyncio.create_task(periodic_cleanup())
    logger.info("Started periodic token cleanup task")

    yield

    # Shutdown - cancel cleanup task
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    logger.info("Stopped periodic token cleanup task")


def create_app() -> FastAPI:
    """Application factory pattern for creating the FastAPI app."""
    app = FastAPI(
        title=settings.app_name,
        openapi_url=f"{settings.api_v1_prefix}/openapi.json",
        docs_url=f"{settings.api_v1_prefix}/docs",
        redoc_url=f"{settings.api_v1_prefix}/redoc",
        lifespan=lifespan,
    )

    # Register exception handlers
    register_exception_handlers(app)

    # Response wrapper middleware (add first so it runs last)
    app.add_middleware(ResponseWrapperMiddleware)

    # CORS middleware - configure for your frontend
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Configure appropriately for production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include API router
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    return app


app = create_app()

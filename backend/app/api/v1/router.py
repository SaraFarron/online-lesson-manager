from fastapi import APIRouter

from app.api.v1.endpoints import auth, health, lessons

api_router = APIRouter()

api_router.include_router(
    health.router,
    prefix="/health",
    tags=["health"],
)

api_router.include_router(
    auth.router,
    prefix="/auth",
    tags=["auth"],
)

api_router.include_router(
    lessons.router,
    prefix="/lessons",
    tags=["lessons"],
)

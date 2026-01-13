from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import DatabaseSession

router = APIRouter()


@router.get("")
async def health_check() -> dict:
    """Basic health check endpoint."""
    return {"status": "healthy"}


@router.get("/db")
async def health_check_db(db: DatabaseSession) -> dict:
    """Health check that verifies database connectivity."""
    try:
        await db.execute(text("SELECT 1"))
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        return {"status": "unhealthy", "database": str(e)}

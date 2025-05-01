# Common, always first
from src.routers.common.cancel import router as cancel_router
from src.routers.common.start import router as start_router
from src.routers.common.help import router as help_router

all_routers = [
    cancel_router,
    start_router,
    help_router,
]

__all__ = ["all_routers"]

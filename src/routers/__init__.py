from routers.common.cancel import router as cancel_router
from routers.common.help import router as help_router
from routers.common.start import router as start_router

all_routers = [
    start_router,
    cancel_router,
    help_router,
]

__all__ = ["all_routers"]

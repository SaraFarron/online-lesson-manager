from routers.check_notify import router as check_notify_router
from routers.common.cancel import router as cancel_router
from routers.common.help import router as help_router
from routers.common.start import router as start_router
from routers.send_to_everyone import router as send_to_every1_router

all_routers = [
    start_router,
    cancel_router,
    help_router,
    send_to_every1_router,
    check_notify_router,
]

__all__ = ["all_routers"]

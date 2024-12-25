from routers.add_lesson import router as add_lesson_router
from routers.check_notify import router as check_notify_router
from routers.common.cancel import router as cancel_router
from routers.common.help import router as help_router
from routers.common.start import router as start_router
from routers.reschedule import router as reschedule_router
from routers.send_to_everyone import router as send_to_every1_router
from routers.set_working_hours import router as set_working_hours_router
from routers.today_schedule import router as today_schedule_router
from routers.week_schedule import router as week_schedule_router
from routers.vacations import  router as vacations_router

all_routers = [
    start_router,
    cancel_router,
    help_router,
    send_to_every1_router,
    check_notify_router,
    today_schedule_router,
    week_schedule_router,
    add_lesson_router,
    set_working_hours_router,
    reschedule_router,
    vacations_router
]

__all__ = ["all_routers"]

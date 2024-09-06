from commands.add_lesson import router as add_lesson_router
from commands.cancel import router as cancel_router
from commands.help import router as help_router

# from commands.reschedule_lesson import router as reschedule_lesson_router
from commands.start import router as start_router
from commands.today_schedule import router as today_schedule_router
from commands.week_schedule import router as week_schedule_router

all_routers = [
    start_router,
    help_router,
    add_lesson_router,
    cancel_router,
    today_schedule_router,
    week_schedule_router,
    # reschedule_lesson_router,
]


__all__ = ["all_routers"]

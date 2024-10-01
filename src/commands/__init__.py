from commands.add_lesson import router as add_lesson_router
from commands.cancel import router as cancel_router
from commands.check_notify import router as check_notify_router
from commands.help import router as help_router
from commands.reschedule import router as reschedule_lesson_router
from commands.send_to_everyone import router as send_to_everyone_router
from commands.set_working_hours import router as set_working_hours_router
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
    reschedule_lesson_router,
    set_working_hours_router,
    check_notify_router,
    send_to_everyone_router,
]


__all__ = ["all_routers"]

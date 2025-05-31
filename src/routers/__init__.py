# Common, always first
from src.routers.common.cancel import router as cancel_router
from src.routers.common.help import router as help_router
from src.routers.common.start import router as start_router

# Lessons
from src.routers.lessons.add_lesson import router as add_lesson_router
from src.routers.lessons.add_recurrent_lesson import router as add_rec_lesson_router
from src.routers.lessons.day_schedule import router as day_schedule_router
from src.routers.lessons.move_lesson import router as move_lesson_router
from src.routers.lessons.week_schedule import router as week_schedule_router
from src.routers.schedule.vacations import router as vacations_router
from src.routers.schedule.work_breaks import router as breaks_router

# Schedule
from src.routers.schedule.work_schedule import router as work_schedule_router
from src.routers.users.notifications import router as notifications_router

# Users
from src.routers.users.profile import router as profile_router

all_routers = [
    cancel_router,
    start_router,
    help_router,
    add_lesson_router,
    add_rec_lesson_router,
    move_lesson_router,
    day_schedule_router,
    week_schedule_router,
    work_schedule_router,
    vacations_router,
    profile_router,
    notifications_router,
    breaks_router,
]

__all__ = ["all_routers"]

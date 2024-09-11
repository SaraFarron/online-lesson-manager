import functools
import logging
from typing import Callable

from aiogram.types import CallbackQuery, Message

from config import logs

logging.basicConfig(
    level=logging.INFO,
    format="%(filename)s:%(lineno)d #%(levelname)-8s [%(asctime)s] - %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logging.getLogger("sqlalchemy.engine.Engine").setLevel(logging.WARNING)


def log_func(func: Callable):
    """Decorator for logging function calls."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):  # noqa: ANN003, ANN002, ANN202
        """Wrapper for function calls."""
        try:
            for arg in args:
                if isinstance(arg, Message):
                    args_str = f"user: {arg.from_user.full_name} text: {arg.text}"
                    logger.info(logs.FUNCTION_CALL, func.__name__, args_str)
                    return func(*args, **kwargs)
                if isinstance(arg, CallbackQuery):
                    args_str = f"user: {arg.from_user.full_name} data: {arg.data}"
                    logger.info(logs.FUNCTION_CALL, func.__name__, args_str)
                    return func(*args, **kwargs)
            logger.info(func.__name__)
            return func(*args, **kwargs)
        except Exception as e:
            logger.exception(logs.FUNCTION_EXP, func.__name__)
            raise e  # noqa: TRY201

    return wrapper

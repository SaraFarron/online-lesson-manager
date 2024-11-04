import functools
from typing import Callable

from aiogram.types import CallbackQuery, Message

from log import logger, logs


def log_func(func: Callable):
    """Decorator for logging function calls."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):  # noqa: ANN003, ANN002, ANN202
        """Wrapper for function calls."""
        try:
            arg = args[0]
            if isinstance(arg, Message):
                username = arg.from_user.full_name if arg.from_user else None
                args_str = f"user: {username} text: {arg.text}"
            elif isinstance(arg, CallbackQuery):
                username = arg.from_user.full_name if arg.from_user else None
                args_str = f"user: {username} data: {arg.data}"
            else:
                logger.warning(logs.UNKNOWN_MESSAGE_TYPE)
                return func(*args, **kwargs)
            logger.info(logs.FUNCTION_CALL, func.__name__, args_str)
            return func(*args, **kwargs)
        except Exception as e:
            logger.exception(logs.FUNCTION_EXP, func.__name__)
            raise e  # noqa: TRY201

    return wrapper

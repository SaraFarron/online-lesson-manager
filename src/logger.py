import functools
import logging
from typing import Callable

from config import logs

logging.basicConfig(
    level=logging.INFO,
    format="%(filename)s:%(lineno)d #%(levelname)-8s [%(asctime)s] - %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)


def log_func(func: Callable):
    """Decorator for logging function calls."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            logger.info(logs.FUNCTION_CALL, func.__name__, args, kwargs)
            return func(*args, **kwargs)
        except Exception as e:
            logger.exception(logs.FUNCTION_EXP, func.__name__)
            raise e

    return wrapper

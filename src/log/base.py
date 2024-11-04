import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(filename)s:%(lineno)d #%(levelname)-8s [%(asctime)s] - %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logging.getLogger("sqlalchemy.engine.Engine").setLevel(logging.WARNING)

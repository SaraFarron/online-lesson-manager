from sqlalchemy import create_engine

from src.core import logs
from src.core.logger import logger
from src.db.models import Base

logger.info(logs.DB_CONNECTING)
engine = create_engine("sqlite:///db/db.sqlite")
Base.metadata.create_all(engine)

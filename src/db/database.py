from sqlalchemy import create_engine

from src.core import logs
from src.db.models import Base
from src.logger import logger

logger.info(logs.DB_CONNECTING)
engine = create_engine("sqlite:///db/db.sqlite")
Base.metadata.create_all(engine)

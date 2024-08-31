from sqlalchemy import create_engine

from src.config import logs
from src.logger import logger
from src.models import Base

logger.info(logs.DB_CONNECTING)
engine = create_engine("sqlite:///db.sqlite", echo=True)
Base.metadata.create_all(engine)

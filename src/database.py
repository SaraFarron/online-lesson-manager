from sqlalchemy import create_engine

from config import logs
from logger import logger
from models import Base

logger.info(logs.DB_CONNECTING)
engine = create_engine("sqlite:///db/db.sqlite")
Base.metadata.create_all(engine)

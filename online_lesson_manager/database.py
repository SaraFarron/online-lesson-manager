from sqlalchemy import create_engine

from online_lesson_manager.logger import logger
from online_lesson_manager.models import Base

logger.info("Connecting to database")
engine = create_engine("sqlite:///db.sqlite", echo=True)
Base.metadata.create_all(engine)

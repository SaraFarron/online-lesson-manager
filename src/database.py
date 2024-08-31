from sqlalchemy import create_engine

from src.logger import logger
from src.models import Base

logger.info("Connecting to database")
engine = create_engine("sqlite:///db.sqlite", echo=True)
Base.metadata.create_all(engine)

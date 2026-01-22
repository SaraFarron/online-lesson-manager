from pydantic import BaseModel


class TimeRangeResponse(BaseModel):
    """Response model for time range schedule."""

    start: str
    end: str

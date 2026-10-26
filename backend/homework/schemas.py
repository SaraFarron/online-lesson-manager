from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class StagedAttachmentPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    file_name: str
    byte_size: int
    expires_at: datetime

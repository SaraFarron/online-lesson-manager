from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class Token(BaseModel):
    access_token: str
    token_type: Literal["bearer"]


class NoContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

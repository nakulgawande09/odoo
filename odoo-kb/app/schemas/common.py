from __future__ import annotations

from pydantic import BaseModel, Field


class PaginationParams(BaseModel):
    limit: int = Field(default=10, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
    code: str | None = None

"""API shapes of the health endpoints."""

from typing import Literal

from pydantic import BaseModel, Field

CheckStatus = Literal["ok", "error"]


class LiveResponse(BaseModel):
    """Body of ``GET /health/live``."""

    status: Literal["ok"]


class ReadyChecks(BaseModel):
    """Result of each dependency check."""

    database: CheckStatus
    redis: CheckStatus


class ReadyResponse(BaseModel):
    """Body of ``GET /health/ready``: 200 when every check is ok, 503 otherwise (same shape)."""

    status: CheckStatus
    checks: ReadyChecks
    version: str = Field(description='Build version: the git SHA, or "dev" locally.')

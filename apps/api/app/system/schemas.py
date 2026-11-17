"""API shapes of the health endpoints."""

from datetime import datetime
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


class JobHealthItem(BaseModel):
    """One scheduled job in ``GET /health/jobs``."""

    name: str = Field(description="Canonical job name, `<owner>.<job>`.")
    interval_seconds: int = Field(
        description="Longest gap between two scheduled runs, from the schedule definition."
    )
    last_success_at: datetime | None = Field(
        description="When the last successful run ended (UTC); null if none is on record."
    )
    stale: bool = Field(
        description=(
            "True when the last success is older than twice `interval_seconds`, or when there is "
            "no successful run on record (a job that never ran is stale)."
        )
    )


class JobsHealthResponse(BaseModel):
    """Body of ``GET /health/jobs``: every scheduled job, sorted by name."""

    jobs: list[JobHealthItem]

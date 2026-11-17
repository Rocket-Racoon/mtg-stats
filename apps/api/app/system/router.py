"""Health endpoints. Thin: parse, call a service, respond."""

from fastapi import APIRouter, Request, Response, status

from app.core.auth import SiteAdminDep
from app.core.db import SessionDep
from app.system import services
from app.system.schemas import JobHealthItem, JobsHealthResponse, LiveResponse, ReadyResponse

router = APIRouter(prefix="/health", tags=["system"])


@router.get("/live", summary="Liveness probe")
async def live() -> LiveResponse:
    """Return 200 while the process runs; contacts no dependency."""
    return services.liveness()


@router.get(
    "/ready",
    summary="Readiness probe",
    responses={503: {"model": ReadyResponse, "description": "A dependency check failed"}},
)
async def ready(request: Request, response: Response) -> ReadyResponse:
    """Return 200 when Postgres and Redis answer, else 503 with the same body shape.

    The 503 keeps the ready body (not the error envelope) so monitors see which check failed.
    """
    report = await services.readiness(request.app.state.settings.git_sha)
    if report.status != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return report


@router.get("/jobs", summary="Scheduled jobs health")
async def jobs(request: Request, session: SessionDep, admin: SiteAdminDep) -> JobsHealthResponse:
    """List each scheduled job with its last successful run; `stale` when it is overdue.

    Site admins only (others get 403 `forbidden`). A bounded report with one entry per
    scheduled job, so it is not paged.
    """
    report = await services.jobs_health(session, request.app.state.job_intervals)
    return JobsHealthResponse(jobs=[JobHealthItem(**vars(item)) for item in report])

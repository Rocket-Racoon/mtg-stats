"""Health endpoints. Thin: parse, call a service, respond."""

from fastapi import APIRouter, Request, Response, status

from app.system import services
from app.system.schemas import LiveResponse, ReadyResponse

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

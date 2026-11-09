"""FastAPI app factory."""

from fastapi import APIRouter, FastAPI

from app.core.config import Settings, get_settings
from app.core.cors import OriginGatedCORSMiddleware

API_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app: routers under ``/api/v1``, OpenAPI and docs there too, and CORS."""
    if settings is None:
        settings = get_settings()
    app = FastAPI(
        title="MTG Stats API",
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=f"{API_PREFIX}/redoc",
        swagger_ui_oauth2_redirect_url=f"{API_PREFIX}/docs/oauth2-redirect",
    )
    app.state.settings = settings
    if settings.cors_allowed_origins:
        app.add_middleware(OriginGatedCORSMiddleware, allowed_origins=settings.cors_allowed_origins)
    api_router = APIRouter(prefix=API_PREFIX)
    # Domain routers are included on this router as their stories land.
    app.include_router(api_router)
    return app


app = create_app()

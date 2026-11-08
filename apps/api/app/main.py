"""FastAPI app factory."""

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import Settings, get_settings

API_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app: routers under ``/api/v1``, OpenAPI and docs there too, and CORS."""
    settings = settings or get_settings()
    app = FastAPI(
        title="MTG Stats API",
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=f"{API_PREFIX}/redoc",
    )
    app.state.settings = settings
    if settings.cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allowed_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    api_router = APIRouter(prefix=API_PREFIX)
    # Domain routers are included on this router as their stories land.
    app.include_router(api_router)
    return app


app = create_app()

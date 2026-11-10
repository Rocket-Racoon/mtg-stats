"""FastAPI app factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from app.core.config import Settings, get_settings
from app.core.cors import OriginGatedCORSMiddleware
from app.core.db import dispose_engine, get_engine
from app.core.errors import ERROR_RESPONSES, UnhandledErrorMiddleware, register_exception_handlers
from app.core.jobs import close_jobs_pool
from app.core.logging import configure_logging
from app.core.openapi import generate_operation_id
from app.core.redis import close_redis, get_redis
from app.core.request_id import RequestIDMiddleware
from app.system.router import router as system_router

API_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Build the engine and Redis client (no I/O), and dispose both on shutdown.

    Creating them here keeps the first ``/ready`` from paying the import and construction cost
    inside its 2 s check budget. No connection is opened until a request needs one.
    """
    get_engine()
    get_redis()
    try:
        yield
    finally:
        try:
            await dispose_engine()
        finally:
            try:
                await close_redis()
            finally:
                await close_jobs_pool()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app: routers under ``/api/v1``, OpenAPI and docs there too, and CORS."""
    if settings is None:
        settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(
        responses=ERROR_RESPONSES,
        title="MTG Stats API",
        generate_unique_id_function=generate_operation_id,
        lifespan=lifespan,
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=f"{API_PREFIX}/redoc",
        swagger_ui_oauth2_redirect_url=f"{API_PREFIX}/docs/oauth2-redirect",
    )
    app.state.settings = settings
    register_exception_handlers(app)
    # add_middleware puts the latest at the outermost position: request ID > CORS > 500 catcher.
    app.add_middleware(UnhandledErrorMiddleware)
    if settings.cors_allowed_origins:
        app.add_middleware(OriginGatedCORSMiddleware, allowed_origins=settings.cors_allowed_origins)
    app.add_middleware(RequestIDMiddleware)
    api_router = APIRouter(prefix=API_PREFIX)
    # Domain routers are included on this router as their stories land.
    api_router.include_router(system_router)
    app.include_router(api_router)
    return app


app = create_app()

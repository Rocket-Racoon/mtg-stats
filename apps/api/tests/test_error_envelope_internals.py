"""Unit tests for error envelope, request-ID and logging internals not covered elsewhere (S-1.4.5).

The acceptance file covers the criteria; this file keeps only the details it does not.
"""

import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError, register_exception_handlers
from app.core.logging import (
    JsonFormatter,
    configure_logging,
    game_id_var,
    log_context,
    set_game_id,
)
from app.main import create_app

ORIGIN = "https://app.example.test"


class _Body(BaseModel):
    name: str


def _app() -> FastAPI:
    settings = Settings(
        APP_ENV="test",
        DATABASE_URL="postgresql+psycopg://u:CHANGE_ME@localhost:5432/x",
        CORS_ALLOWED_ORIGINS=ORIGIN,
    )
    app = create_app(settings)

    @app.post("/t/body")
    async def body(payload: _Body) -> dict[str, str]:
        return {"name": payload.name}

    @app.get("/t/conflict")
    async def conflict() -> None:
        raise ConflictError

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app(), raise_server_exceptions=False)


def test_unknown_route_uses_not_found_envelope(client: TestClient) -> None:
    resp = client.get("/api/v1/nope", headers={"X-Request-ID": "abc-1"})
    assert resp.status_code == 404
    assert resp.json() == {
        "error": {
            "code": "not_found",
            "message": "Not Found",
            "details": None,
            "request_id": "abc-1",
        }
    }
    assert resp.headers["X-Request-ID"] == "abc-1"


def test_method_not_allowed_keeps_allow_header(client: TestClient) -> None:
    resp = client.get("/t/body")
    assert resp.status_code == 405
    assert resp.json()["error"]["code"] == "method_not_allowed"
    assert "POST" in resp.headers["allow"]


def test_disallowed_preflight_still_has_request_id_and_envelope(client: TestClient) -> None:
    resp = client.options(
        "/t/conflict",
        headers={"Origin": "https://evil.test", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in resp.headers
    assert resp.headers["X-Request-ID"]
    assert resp.json()["error"]["code"] == "method_not_allowed"


def test_json_formatter_includes_context() -> None:
    record = logging.LogRecord("svc", logging.WARNING, __file__, 1, "hi %s", ("there",), None)
    with log_context(request_id="r1"):
        token = set_game_id("g1")
        try:
            data = json.loads(JsonFormatter().format(record))
        finally:
            game_id_var.reset(token)
    assert data["message"] == "hi there"
    assert (data["level"], data["logger"]) == ("WARNING", "svc")
    assert data["timestamp"].endswith("+00:00")
    assert (data["request_id"], data["game_id"]) == ("r1", "g1")
    assert "job_id" not in data
    outside = json.loads(JsonFormatter().format(record))
    assert "request_id" not in outside


def test_configure_logging_is_idempotent() -> None:
    configure_logging()
    configure_logging()
    marked = [h for h in logging.getLogger().handlers if getattr(h, "_mtg_stats_json_handler", 0)]
    assert len(marked) == 1


def test_log_context_job_id_is_formatted_into_the_line() -> None:
    record = logging.LogRecord("w", logging.INFO, __file__, 1, "m", None, None)
    with log_context(job_id="j7"):
        assert json.loads(JsonFormatter().format(record))["job_id"] == "j7"


def test_register_handlers_on_bare_app() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/x")
    async def x() -> None:
        raise NotFoundError

    assert TestClient(app).get("/x").json()["error"]["code"] == "not_found"

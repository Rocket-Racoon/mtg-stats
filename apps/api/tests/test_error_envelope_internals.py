"""Unit tests for the error envelope, request-ID and logging internals (S-1.4.5)."""

import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.config import Settings
from app.core.errors import (
    ConflictError,
    DomainError,
    NotFoundError,
    RateLimitedError,
    register_exception_handlers,
)
from app.core.logging import (
    JsonFormatter,
    configure_logging,
    game_id_var,
    log_context,
    set_game_id,
)
from app.core.request_id import resolve_request_id
from app.main import create_app

ORIGIN = "https://app.example.test"


class _Body(BaseModel):
    name: str


class _TeapotError(DomainError):
    code = "teapot"
    status_code = 418


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

    @app.get("/t/teapot")
    async def teapot() -> None:
        raise _TeapotError("short and stout", details={"spout": True})

    @app.get("/t/conflict")
    async def conflict() -> None:
        raise ConflictError

    @app.get("/t/boom")
    async def boom() -> None:
        raise RuntimeError("secret internals")

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


def test_validation_error_lists_field_paths(client: TestClient) -> None:
    resp = client.post("/t/body", json={})
    err = resp.json()["error"]
    assert resp.status_code == 422
    assert err["code"] == "validation_error"
    assert err["details"]["fields"][0]["path"] == "body.name"
    assert err["details"]["fields"][0]["message"]


def test_domain_error_uses_its_code_status_and_details(client: TestClient) -> None:
    resp = client.get("/t/teapot")
    assert resp.status_code == 418
    err = resp.json()["error"]
    assert (err["code"], err["message"], err["details"]) == (
        "teapot",
        "short and stout",
        {"spout": True},
    )
    assert client.get("/t/conflict").json()["error"]["code"] == "conflict"


def test_base_error_codes_and_statuses() -> None:
    assert (NotFoundError.code, NotFoundError.status_code) == ("not_found", 404)
    assert RateLimitedError.status_code == 429


def test_unhandled_exception_is_500_without_leaks_and_logged_with_id(
    client: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    resp = client.get("/t/boom", headers={"X-Request-ID": "rid.9"})
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "internal_error"
    assert resp.json()["error"]["request_id"] == "rid.9"
    assert "secret internals" not in resp.text
    assert "Traceback" not in resp.text
    lines = [json.loads(x) for x in capsys.readouterr().err.splitlines() if x.startswith("{")]
    failure = next(x for x in lines if x["message"] == "Unhandled exception")
    assert failure["request_id"] == "rid.9"
    assert "RuntimeError: secret internals" in failure["exception"]


def test_500_carries_cors_and_request_id_headers(client: TestClient) -> None:
    resp = client.get("/t/boom", headers={"Origin": ORIGIN})
    assert resp.status_code == 500
    assert resp.headers["access-control-allow-origin"] == ORIGIN
    assert resp.headers["X-Request-ID"] == resp.json()["error"]["request_id"]


def test_cors_exposes_request_id_header(client: TestClient) -> None:
    resp = client.get("/t/conflict", headers={"Origin": ORIGIN})
    assert "x-request-id" in resp.headers["access-control-expose-headers"].lower()


def test_disallowed_preflight_still_has_request_id_and_envelope(client: TestClient) -> None:
    resp = client.options(
        "/t/conflict",
        headers={"Origin": "https://evil.test", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in resp.headers
    assert resp.headers["X-Request-ID"]
    assert resp.json()["error"]["code"] == "method_not_allowed"


@pytest.mark.parametrize("bad", ["a b", "x" * 129, "bad/slash", "é", "a;b"])
def test_unsafe_request_ids_are_replaced(bad: str) -> None:
    new = resolve_request_id(bad)
    assert new != bad
    assert len(new) == 32


def test_safe_request_id_is_kept_and_max_length_ok() -> None:
    assert resolve_request_id("A.b_c-9") == "A.b_c-9"
    assert resolve_request_id("x" * 128) == "x" * 128
    assert resolve_request_id(None) != resolve_request_id(None)


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


def test_job_context_and_configure_logging_idempotent() -> None:
    configure_logging()
    configure_logging()
    marked = [h for h in logging.getLogger().handlers if getattr(h, "_mtg_stats_json_handler", 0)]
    assert len(marked) == 1
    record = logging.LogRecord("w", logging.INFO, __file__, 1, "m", None, None)
    with log_context(job_id="j7"):
        assert json.loads(JsonFormatter().format(record))["job_id"] == "j7"


def test_error_response_is_declared_in_openapi(client: TestClient) -> None:
    spec = client.get("/api/v1/openapi.json").json()
    assert "ErrorResponse" in spec["components"]["schemas"]
    post = spec["paths"]["/t/body"]["post"]["responses"]
    for status in ("401", "403", "404", "409", "422", "429", "500"):
        assert post[status]["content"]["application/json"]["schema"]["$ref"].endswith(
            "/ErrorResponse"
        )


def test_register_handlers_on_bare_app() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/x")
    async def x() -> None:
        raise NotFoundError

    assert TestClient(app).get("/x").json()["error"]["code"] == "not_found"

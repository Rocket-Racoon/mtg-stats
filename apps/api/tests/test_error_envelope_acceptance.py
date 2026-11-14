"""Acceptance tests for S-1.4.5: error envelope, request IDs and structured JSON logs."""

import ast
import json
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.config import Settings
from app.core.errors import (
    ERROR_RESPONSES,
    AuthenticationError,
    ConflictError,
    DomainError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitedError,
)
from app.core.logging import JsonFormatter, log_context, set_game_id
from app.main import create_app

ORIGIN = "https://app.example.test"
DB_URL = "postgresql+psycopg://u:CHANGE_ME@localhost:5432/x"
SNAKE = re.compile(r"^[a-z][a-z0-9_]*$")
SECRET_TEXT = "secret-ish text"
APP_ROOT = Path(__file__).resolve().parent.parent / "app"


class _Payload(BaseModel):
    name: str
    count: int


class _TeapotError(DomainError):
    code = "teapot_brewing"
    status_code = 418
    default_message = "Short and stout."


def _service_rejects() -> None:
    """Stands in for a domain service: raises a DomainError, never HTTPException."""
    raise _TeapotError(details={"spout": "left"})


class _Capture(logging.Handler):
    """Collects every record as the JSON line JsonFormatter would write."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.setFormatter(JsonFormatter())
        self.lines: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.name == "httpx":  # the test client's own log, outside the request
            return
        self.lines.append(json.loads(self.format(record)))


@contextmanager
def capture_logs() -> Iterator[_Capture]:
    handler = _Capture()
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield handler
    finally:
        root.removeHandler(handler)


def _build(env: str = "test", origins: str = "") -> FastAPI:
    settings = Settings(
        APP_ENV=env,
        DATABASE_URL=DB_URL,
        PUBLIC_WEB_URL="https://app.example.com",  # prod requires https
        CORS_ALLOWED_ORIGINS=origins,
    )
    app = create_app(settings)

    @app.get("/p/401")
    async def p401() -> None:
        raise AuthenticationError

    @app.get("/p/403")
    async def p403() -> None:
        raise PermissionDeniedError

    @app.get("/p/404")
    async def p404() -> None:
        raise NotFoundError

    @app.get("/p/409")
    async def p409() -> None:
        raise ConflictError

    @app.get("/p/429")
    async def p429() -> None:
        raise RateLimitedError

    @app.get("/p/500")
    async def p500() -> None:
        raise RuntimeError(SECRET_TEXT)

    @app.post("/p/422")
    async def p422(payload: _Payload) -> dict[str, str]:
        return {"name": payload.name}

    @app.get("/p/service")
    async def pservice() -> None:
        _service_rejects()

    @app.get("/p/ok")
    async def pok() -> dict[str, str]:
        logging.getLogger("probe").info("inside request")
        return {"ok": "yes"}

    @app.get("/p/game")
    async def pgame() -> dict[str, str]:
        set_game_id("game-42")
        logging.getLogger("probe").info("with game")
        return {"ok": "yes"}

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_build(), raise_server_exceptions=False)


def _assert_envelope(resp: Any, code: str | None = None) -> dict[str, Any]:
    body = resp.json()
    assert set(body) == {"error"}
    err = body["error"]
    assert set(err) == {"code", "message", "details", "request_id"}
    assert SNAKE.match(err["code"])
    assert isinstance(err["message"], str)
    assert err["details"] is None or isinstance(err["details"], dict)
    assert isinstance(err["request_id"], str)
    assert err["request_id"]
    assert err["request_id"] == resp.headers["X-Request-ID"]
    if code is not None:
        assert err["code"] == code
    return err


# ---------------------------------------------------------------- AC1


@pytest.mark.parametrize(
    ("path", "method", "status", "code"),
    [
        ("/p/401", "get", 401, "unauthenticated"),
        ("/p/403", "get", 403, "forbidden"),
        ("/p/404", "get", 404, "not_found"),
        ("/p/409", "get", 409, "conflict"),
        ("/p/422", "post", 422, "validation_error"),
        ("/p/429", "get", 429, "rate_limited"),
        ("/p/500", "get", 500, "internal_error"),
    ],
)
def test_error_status_returns_exact_envelope(
    client: TestClient, path: str, method: str, status: int, code: str
) -> None:
    resp = getattr(client, method)(path)
    assert resp.status_code == status
    _assert_envelope(resp, code)


def test_unknown_route_returns_404_envelope(client: TestClient) -> None:
    resp = client.get("/api/v1/does-not-exist")
    assert resp.status_code == 404
    _assert_envelope(resp, "not_found")


def test_wrong_method_returns_405_envelope(client: TestClient) -> None:
    resp = client.post("/p/401")
    assert resp.status_code == 405
    _assert_envelope(resp, "method_not_allowed")


# ---------------------------------------------------------------- AC2


def test_validation_error_lists_each_failing_field_path_with_message(
    client: TestClient,
) -> None:
    resp = client.post("/p/422", json={"count": "not-a-number-xyz"})
    assert resp.status_code == 422
    err = _assert_envelope(resp, "validation_error")
    fields = err["details"]["fields"]
    by_path = {f["path"]: f["message"] for f in fields}
    assert "body.name" in by_path
    assert "body.count" in by_path
    assert len(fields) == 2
    assert all(isinstance(m, str) and m for m in by_path.values())


def test_validation_error_does_not_echo_input_values(client: TestClient) -> None:
    resp = client.post("/p/422", json={"count": "not-a-number-xyz"})
    assert "not-a-number-xyz" not in resp.text


# ---------------------------------------------------------------- AC3


def test_unhandled_exception_returns_500_without_leaking_details(
    client: TestClient,
) -> None:
    resp = client.get("/p/500")
    assert resp.status_code == 500
    err = _assert_envelope(resp, "internal_error")
    assert err["details"] is None
    assert SECRET_TEXT not in resp.text
    assert "Traceback" not in resp.text
    assert "RuntimeError" not in resp.text


def test_unhandled_exception_traceback_is_logged_with_same_request_id(
    client: TestClient,
) -> None:
    with capture_logs() as logs:
        resp = client.get("/p/500", headers={"X-Request-ID": "trace-me-1"})
    assert resp.json()["error"]["request_id"] == "trace-me-1"
    tracebacks = [line for line in logs.lines if "Traceback" in line.get("exception", "")]
    assert tracebacks
    assert all(line["request_id"] == "trace-me-1" for line in tracebacks)
    assert SECRET_TEXT in tracebacks[0]["exception"]
    assert "RuntimeError" in tracebacks[0]["exception"]


def test_500_keeps_cors_and_request_id_headers_for_allowed_origin() -> None:
    c = TestClient(_build("prod", ORIGIN), raise_server_exceptions=False)
    resp = c.get("/p/500", headers={"Origin": ORIGIN})
    assert resp.status_code == 500
    assert resp.headers["access-control-allow-origin"] == ORIGIN
    assert resp.headers["X-Request-ID"] == resp.json()["error"]["request_id"]


# ---------------------------------------------------------------- AC4


@pytest.mark.parametrize("rid", ["abc-123_X.y", "a", "A" * 128])
def test_valid_request_id_header_is_used_and_echoed(client: TestClient, rid: str) -> None:
    resp = client.get("/p/ok", headers={"X-Request-ID": rid})
    assert resp.headers["X-Request-ID"] == rid


@pytest.mark.parametrize(
    "rid",
    [
        b"A" * 129,
        b"has space",
        b"a/b",
        b"",
        "café".encode(),
        b" ",
    ],
)
def test_invalid_request_id_header_is_replaced_with_generated_id(
    client: TestClient, rid: bytes
) -> None:
    resp = client.get("/p/ok", headers={"X-Request-ID": rid})  # type: ignore[dict-item]
    new = resp.headers["X-Request-ID"]
    assert new.encode() != rid
    assert re.fullmatch(r"[A-Za-z0-9._-]{1,128}", new)


def test_missing_request_id_header_generates_unique_ids(client: TestClient) -> None:
    a = client.get("/p/ok").headers["X-Request-ID"]
    b = client.get("/p/ok").headers["X-Request-ID"]
    assert a
    assert b
    assert a != b


def test_every_log_line_of_a_request_carries_its_request_id(client: TestClient) -> None:
    with capture_logs() as logs:
        client.get("/p/ok", headers={"X-Request-ID": "log-me-7"})
    assert any(line["message"] == "inside request" for line in logs.lines)
    assert any(line["logger"] == "app.access" for line in logs.lines)
    assert all(line["request_id"] == "log-me-7" for line in logs.lines)


def test_generated_request_id_appears_in_logs_and_header(client: TestClient) -> None:
    with capture_logs() as logs:
        resp = client.get("/p/ok")
    rid = resp.headers["X-Request-ID"]
    assert logs.lines
    assert all(line["request_id"] == rid for line in logs.lines)


def test_request_id_header_is_exposed_to_browsers_via_cors() -> None:
    c = TestClient(_build("prod", ORIGIN), raise_server_exceptions=False)
    resp = c.get("/p/ok", headers={"Origin": ORIGIN})
    exposed = [h.strip().lower() for h in resp.headers["access-control-expose-headers"].split(",")]
    assert "x-request-id" in exposed


# ---------------------------------------------------------------- AC5


def test_log_line_is_one_json_object_with_required_keys(client: TestClient) -> None:
    with capture_logs() as logs:
        client.get("/p/ok", headers={"X-Request-ID": "shape-1"})
    line = next(entry for entry in logs.lines if entry["message"] == "inside request")
    assert {"timestamp", "level", "logger", "message", "request_id"} <= set(line)
    assert line["level"] == "INFO"
    assert line["logger"] == "probe"
    assert line["request_id"] == "shape-1"


def test_log_timestamp_is_utc(client: TestClient) -> None:
    with capture_logs() as logs:
        client.get("/p/ok")
    ts = logs.lines[0]["timestamp"]
    assert ts.endswith(("+00:00", "Z"))
    parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    assert parsed.utcoffset() == timedelta(0)


def test_stderr_output_is_json_lines_with_context(
    client: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    client.get("/p/ok", headers={"X-Request-ID": "stderr-1"})
    out = [ln for ln in capsys.readouterr().err.splitlines() if ln.strip()]
    assert out
    parsed = [json.loads(ln) for ln in out]
    assert any(p.get("request_id") == "stderr-1" for p in parsed)


def test_traceback_stays_on_one_json_line(
    client: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    client.get("/p/500")
    lines = [ln for ln in capsys.readouterr().err.splitlines() if ln.strip()]
    assert lines
    for ln in lines:
        json.loads(ln)


def test_log_context_job_id_appears_on_lines() -> None:
    with capture_logs() as logs, log_context(job_id="job-9"):
        logging.getLogger("worker").info("running")
    assert logs.lines[0]["job_id"] == "job-9"
    assert "request_id" not in logs.lines[0]


def test_job_id_is_absent_after_context_exits() -> None:
    with capture_logs() as logs:
        with log_context(job_id="job-9"):
            pass
        logging.getLogger("worker").info("after")
    assert "job_id" not in logs.lines[0]


def test_set_game_id_adds_game_id_to_log_lines(client: TestClient) -> None:
    with capture_logs() as logs:
        client.get("/p/game", headers={"X-Request-ID": "g-1"})
    line = next(entry for entry in logs.lines if entry["message"] == "with game")
    assert line["game_id"] == "game-42"
    assert line["request_id"] == "g-1"


def test_game_id_does_not_leak_into_the_next_request(client: TestClient) -> None:
    client.get("/p/game")
    with capture_logs() as logs:
        client.get("/p/ok")
    assert logs.lines
    assert all("game_id" not in line for line in logs.lines)


def test_request_id_does_not_leak_after_request(client: TestClient) -> None:
    client.get("/p/ok", headers={"X-Request-ID": "gone-1"})
    with capture_logs() as logs:
        logging.getLogger("after").info("outside")
    assert "request_id" not in logs.lines[0]


# ---------------------------------------------------------------- AC6


def test_custom_domain_error_from_service_uses_its_code_and_status(
    client: TestClient,
) -> None:
    resp = client.get("/p/service")
    assert resp.status_code == 418
    err = _assert_envelope(resp, "teapot_brewing")
    assert err["message"] == "Short and stout."
    assert err["details"] == {"spout": "left"}


@pytest.mark.parametrize(
    ("cls", "code", "status"),
    [
        (NotFoundError, "not_found", 404),
        (ConflictError, "conflict", 409),
        (PermissionDeniedError, "forbidden", 403),
        (AuthenticationError, "unauthenticated", 401),
        (RateLimitedError, "rate_limited", 429),
    ],
)
def test_builtin_domain_errors_have_documented_code_and_status(
    cls: type[DomainError], code: str, status: int
) -> None:
    assert issubclass(cls, DomainError)
    assert cls.code == code
    assert cls.status_code == status


def find_http_exception_refs(root: Path) -> list[str]:
    """Return files under any ``services`` dir below ``root`` that mention HTTPException."""
    offenders: list[str] = []
    for path in root.glob("**/services/**/*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.ImportFrom | ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.Name):
                names = [node.id]
            elif isinstance(node, ast.Attribute):
                names = [node.attr]
            if "HTTPException" in names:
                offenders.append(str(path.relative_to(root)))
                break
    return offenders


def test_services_never_reference_http_exception() -> None:
    assert find_http_exception_refs(APP_ROOT) == []


# ---------------------------------------------------------------- OpenAPI


def test_openapi_declares_error_response_schema(client: TestClient) -> None:
    spec = client.get("/api/v1/openapi.json").json()
    schemas = spec["components"]["schemas"]
    assert "ErrorResponse" in schemas
    assert "ErrorBody" in schemas
    assert set(schemas["ErrorBody"]["properties"]) == {
        "code",
        "message",
        "details",
        "request_id",
    }


def test_openapi_probe_route_lists_error_response_for_422_and_500(
    client: TestClient,
) -> None:
    spec = client.get("/api/v1/openapi.json").json()
    responses = spec["paths"]["/p/422"]["post"]["responses"]
    for status in ("401", "403", "404", "409", "422", "429", "500"):
        ref = responses[status]["content"]["application/json"]["schema"]["$ref"]
        assert ref.endswith("/ValidationErrorResponse" if status == "422" else "/ErrorResponse")


def test_error_responses_constant_covers_all_documented_statuses() -> None:
    assert set(ERROR_RESPONSES) == {401, 403, 404, 409, 422, 429, 500, "4XX", "5XX"}

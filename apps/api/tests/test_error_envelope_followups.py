"""S-1.4.5 review follow-ups: headers, 5xx hygiene, typed details, access line, log level."""

import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from app.core.config import Settings
from app.core.errors import AuthenticationError, DomainError, RateLimitedError
from app.main import create_app
from tests.test_error_envelope_acceptance import DB_URL, capture_logs, find_http_exception_refs


class _Body(BaseModel):
    name: str
    count: int


class _HeaderError(DomainError):
    code = "with_headers"
    status_code = 409


def _build(**settings: Any) -> FastAPI:
    app = create_app(Settings(APP_ENV="test", DATABASE_URL=DB_URL, **settings))

    @app.get("/h/domain")
    async def h_domain() -> None:
        raise _HeaderError(headers={"X-Custom": "yes"})

    @app.get("/h/auth")
    async def h_auth() -> None:
        raise AuthenticationError

    @app.get("/h/rate")
    async def h_rate() -> None:
        raise RateLimitedError(retry_after=30)

    @app.get("/h/http/{status}")
    async def h_http(status: int) -> None:
        raise HTTPException(status, "db password=xyz")

    @app.get("/h/http-headers/{status}")
    async def h_http_headers(status: int) -> None:
        raise HTTPException(status, "detail", headers={"X-Extra": "kept"})

    @app.post("/h/body")
    async def h_body(payload: _Body) -> None:
        return None

    @app.get("/h/stream")
    async def h_stream() -> StreamingResponse:
        async def gen() -> AsyncIterator[bytes]:
            yield b"chunk"
            raise RuntimeError("mid-stream")

        return StreamingResponse(gen())

    @app.get("/h/debug")
    async def h_debug() -> dict[str, str]:
        logging.getLogger("probe").debug("debug line")
        logging.getLogger("probe").info("info line", extra={"evil": "x"})
        return {}

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_build(), raise_server_exceptions=False)


def _err(resp: Any) -> dict[str, Any]:
    err: dict[str, Any] = resp.json()["error"]
    assert err["request_id"] == resp.headers["X-Request-ID"]
    return err


# ------------------------------------------------------------ 1. DomainError headers


def test_domain_error_subclass_sends_its_headers(client: TestClient) -> None:
    resp = client.get("/h/domain")
    assert resp.status_code == 409
    assert resp.headers["X-Custom"] == "yes"


def test_authentication_error_sends_www_authenticate_bearer(client: TestClient) -> None:
    resp = client.get("/h/auth")
    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"] == "Bearer"


def test_rate_limited_error_sends_retry_after(client: TestClient) -> None:
    resp = client.get("/h/rate")
    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == "30"


# ------------------------------------------------------------ 2. 5xx hygiene


def test_http_exception_500_hides_detail(client: TestClient) -> None:
    resp = client.get("/h/http/500")
    assert resp.status_code == 500
    err = _err(resp)
    assert err["code"] == "internal_error"
    assert err["message"] == "An internal error occurred."
    assert "xyz" not in resp.text


def test_http_exception_503_is_service_unavailable(client: TestClient) -> None:
    resp = client.get("/h/http/503")
    assert resp.status_code == 503
    assert _err(resp)["code"] == "service_unavailable"
    assert "xyz" not in resp.text


def test_http_exception_502_is_internal_error(client: TestClient) -> None:
    resp = client.get("/h/http/502")
    assert resp.status_code == 502
    assert _err(resp)["code"] == "internal_error"
    assert "xyz" not in resp.text


def test_http_exception_5xx_keeps_custom_headers(client: TestClient) -> None:
    resp = client.get("/h/http-headers/503")
    assert resp.status_code == 503
    assert resp.headers["X-Extra"] == "kept"


# ------------------------------------------------------------ 3. HTTPException mapping


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, "bad_request"),
        (401, "unauthenticated"),
        (403, "forbidden"),
        (409, "conflict"),
        (413, "payload_too_large"),
        (415, "unsupported_media_type"),
        (429, "rate_limited"),
        (418, "http_error"),
    ],
)
def test_http_exception_status_maps_to_code(client: TestClient, status: int, code: str) -> None:
    resp = client.get(f"/h/http/{status}")
    assert resp.status_code == status
    assert _err(resp)["code"] == code


def test_http_exception_4xx_keeps_custom_headers(client: TestClient) -> None:
    resp = client.get("/h/http-headers/403")
    assert resp.status_code == 403
    assert resp.headers["X-Extra"] == "kept"


def test_unknown_route_outside_api_prefix_gets_404_envelope(client: TestClient) -> None:
    resp = client.get("/nope-outside")
    assert resp.status_code == 404
    assert _err(resp)["code"] == "not_found"


def test_exception_after_response_started_is_logged_with_request_id_without_envelope(
    client: TestClient,
) -> None:
    with capture_logs() as logs:
        resp = client.get("/h/stream", headers={"X-Request-ID": "stream-1"})
    assert resp.status_code == 200
    assert "error" not in resp.text
    assert "mid-stream" not in resp.text
    failed = [x for x in logs.lines if x["message"] == "Unhandled exception"]
    assert failed
    assert all(x["request_id"] == "stream-1" for x in failed)
    assert "mid-stream" in failed[0]["exception"]


# ------------------------------------------------------------ 4. typed validation details


def test_validation_fields_have_loc_path_and_message(client: TestClient) -> None:
    resp = client.post("/h/body", json={})
    assert resp.status_code == 422
    fields = _err(resp)["details"]["fields"]
    assert len(fields) == 2
    for f in fields:
        assert set(f) == {"loc", "path", "message"}
        assert isinstance(f["loc"], list)
        assert f["path"] == ".".join(str(p) for p in f["loc"])
        assert f["message"]
    assert {f["path"] for f in fields} == {"body.name", "body.count"}


def test_openapi_422_schema_is_typed_validation_error_response(client: TestClient) -> None:
    spec = client.get("/api/v1/openapi.json").json()
    schemas = spec["components"]["schemas"]
    content = spec["paths"]["/h/body"]["post"]["responses"]["422"]["content"]
    assert content["application/json"]["schema"]["$ref"].endswith("/ValidationErrorResponse")
    assert set(schemas["ValidationField"]["properties"]) == {"loc", "path", "message"}
    assert "fields" in schemas["ValidationDetails"]["properties"]


# ------------------------------------------------------------ 5. structured access line


def test_access_line_has_structured_top_level_fields(client: TestClient) -> None:
    with capture_logs() as logs:
        client.get("/h/http/404", headers={"X-Request-ID": "acc-1"})
    line = next(x for x in logs.lines if x["logger"] == "app.access")
    assert line["method"] == "GET"
    assert line["path"] == "/h/http/404"
    assert line["status"] == 404
    assert isinstance(line["status"], int)
    assert isinstance(line["duration_ms"], int | float)
    assert not isinstance(line["duration_ms"], bool)
    assert line["request_id"] == "acc-1"


def test_non_whitelisted_extra_keys_are_not_emitted(client: TestClient) -> None:
    with capture_logs() as logs:
        client.get("/h/debug")
    line = next(x for x in logs.lines if x["message"] == "info line")
    assert "evil" not in line


# ------------------------------------------------------------ 6. snake_case guard


@pytest.mark.parametrize("bad", ["BadCode", "bad-code", "1bad", "", "bad code"])
def test_domain_error_subclass_with_non_snake_case_code_raises_type_error(bad: str) -> None:
    with pytest.raises(TypeError):
        type("Bad", (DomainError,), {"code": bad})


def test_domain_error_subclass_with_snake_case_code_is_accepted() -> None:
    cls = type("Good", (DomainError,), {"code": "good_code_2"})
    assert cls.code == "good_code_2"


# ------------------------------------------------------------ 7. LOG_LEVEL


def test_log_level_debug_lets_debug_lines_through(capsys: pytest.CaptureFixture[str]) -> None:
    c = TestClient(_build(LOG_LEVEL="DEBUG"), raise_server_exceptions=False)
    c.get("/h/debug")
    lines = [json.loads(x) for x in capsys.readouterr().err.splitlines() if x.startswith("{")]
    assert any(x["message"] == "debug line" and x["level"] == "DEBUG" for x in lines)


def test_default_log_level_filters_debug_lines(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    c = TestClient(_build(), raise_server_exceptions=False)
    c.get("/h/debug")
    lines = [json.loads(x) for x in capsys.readouterr().err.splitlines() if x.startswith("{")]
    assert any(x["message"] == "info line" for x in lines)
    assert not any(x["message"] == "debug line" for x in lines)


def test_invalid_log_level_fails_settings_validation_naming_log_level() -> None:
    with pytest.raises(ValidationError) as info:
        Settings(APP_ENV="test", DATABASE_URL=DB_URL, LOG_LEVEL="LOUD")
    assert "LOG_LEVEL" in str(info.value)


# ------------------------------------------------------------ 8. AST guard self-test


@pytest.mark.parametrize(
    "source",
    [
        "from fastapi import HTTPException\n",
        "import fastapi\nraise fastapi.HTTPException(400)\n",
        "from starlette.exceptions import HTTPException as H\n",
        "def f():\n    raise HTTPException(404)\n",
    ],
)
def test_http_exception_guard_flags_offending_service_file(tmp_path: Path, source: str) -> None:
    services = tmp_path / "games" / "services"
    services.mkdir(parents=True)
    (services / "bad.py").write_text(source, encoding="utf-8")
    assert find_http_exception_refs(tmp_path) == [str(Path("games/services/bad.py"))]


def test_http_exception_guard_ignores_clean_service_and_non_service_files(
    tmp_path: Path,
) -> None:
    services = tmp_path / "games" / "services"
    services.mkdir(parents=True)
    (services / "ok.py").write_text("from app.core.errors import NotFoundError\n")
    (tmp_path / "games" / "router.py").write_text("from fastapi import HTTPException\n")
    assert find_http_exception_refs(tmp_path) == []

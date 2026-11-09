"""Unit tests for the OpenAPI export and operation ID convention (S-1.5.1)."""

from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI

from app.core.openapi import assert_unique_operation_ids, generate_operation_id
from app.main import app
from app.scripts.export_openapi import DEFAULT_OUTPUT, main


def _probe() -> FastAPI:
    probe = FastAPI(generate_unique_id_function=generate_operation_id)

    async def ping() -> dict[str, str]:
        return {}

    probe.add_api_route("/a", ping, methods=["GET"])
    probe.add_api_route("/b", ping, methods=["GET"])
    return probe


def test_real_app_operation_ids_are_unique() -> None:
    assert_unique_operation_ids(app)


def test_duplicate_operation_ids_name_both_routes() -> None:
    with pytest.raises(ValueError, match=r"system_ping.*GET /a.*GET /b"):
        assert_unique_operation_ids(_probe())


def test_operation_id_uses_first_tag_as_domain() -> None:
    probe = FastAPI(generate_unique_id_function=generate_operation_id)
    router = APIRouter(tags=["Decks"])

    @router.get("/x")
    async def list_things() -> list[str]:
        return []

    probe.include_router(router)
    assert probe.openapi()["paths"]["/x"]["get"]["operationId"] == "decks_list_things"


def test_export_is_byte_identical_and_check_detects_drift(tmp_path: Path) -> None:
    out = tmp_path / "openapi.json"
    assert main(["--output", str(out)]) == 0
    first = out.read_bytes()
    assert main(["--output", str(out)]) == 0
    assert out.read_bytes() == first
    assert first.endswith(b"\n")
    assert main(["--output", str(out), "--check"]) == 0
    out.write_bytes(first + b" ")
    assert main(["--output", str(out), "--check"]) == 1
    assert main(["--output", str(tmp_path / "missing.json"), "--check"]) == 1


def test_committed_spec_matches_app() -> None:
    assert main(["--output", str(DEFAULT_OUTPUT), "--check"]) == 0

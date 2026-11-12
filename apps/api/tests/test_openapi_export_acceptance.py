"""Acceptance tests for S-1.5.1: offline OpenAPI export and operation ID convention."""

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute

import app.main as app_main
from app.core.openapi import (
    DuplicateOperationIdError,
    assert_unique_operation_ids,
    collect_operation_ids,
    generate_operation_id,
    route_domain,
)
from app.scripts import export_openapi
from app.scripts.export_openapi import default_output, main

API_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = API_DIR.parents[1]
OPERATION_ID_RE = re.compile(r"^[a-z][a-z0-9]*_[a-z0-9_]+$")


def _new_app() -> FastAPI:
    return FastAPI(generate_unique_id_function=generate_operation_id)


def _search_in_module_a() -> Callable[..., Any]:
    async def search() -> list[str]:
        return []

    return search


def _search_in_module_b() -> Callable[..., Any]:
    async def search() -> dict[str, str]:
        return {}

    return search


def _duplicate_probe() -> FastAPI:
    """Two routers, both tagged ``cards``, each with an endpoint function named ``search``."""
    probe = _new_app()
    first = APIRouter(tags=["cards"])
    second = APIRouter(tags=["cards"])
    first.add_api_route("/cards/search", _search_in_module_a(), methods=["GET"])
    second.add_api_route("/cards/lookup", _search_in_module_b(), methods=["POST"])
    probe.include_router(first)
    probe.include_router(second)
    return probe


def _route(app: FastAPI, path: str) -> APIRoute:
    return next(r for r in app.routes if isinstance(r, APIRoute) and r.path == path)


# ---------------------------------------------------------------- AC1: byte-identical export


def test_export_run_twice_writes_byte_identical_files(tmp_path: Path) -> None:
    one, two = tmp_path / "one.json", tmp_path / "two.json"
    assert main(["--output", str(one)]) == 0
    assert main(["--output", str(two)]) == 0
    assert one.read_bytes() == two.read_bytes()
    assert one.stat().st_size > 0


def test_export_overwriting_the_same_file_twice_is_byte_identical(tmp_path: Path) -> None:
    out = tmp_path / "nested" / "openapi.json"
    assert main(["--output", str(out)]) == 0
    first = out.read_bytes()
    assert main(["--output", str(out)]) == 0
    assert out.read_bytes() == first


def test_export_keys_are_sorted_at_every_level(tmp_path: Path) -> None:
    out = tmp_path / "openapi.json"
    assert main(["--output", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    parsed = json.loads(text)
    assert json.dumps(parsed, sort_keys=True, indent=2, ensure_ascii=False) + "\n" == text

    def check(node: object) -> None:
        if isinstance(node, dict):
            assert list(node) == sorted(node)
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)

    check(parsed)


def test_export_uses_two_space_indent_trailing_newline_and_lf_only(tmp_path: Path) -> None:
    out = tmp_path / "openapi.json"
    assert main(["--output", str(out)]) == 0
    data = out.read_bytes()
    assert data.endswith(b"}\n")
    assert not data.endswith(b"\n\n")
    assert b"\r" not in data
    assert b"\t" not in data
    assert data.startswith(b'{\n  "')
    indents = {len(line) - len(line.lstrip(" ")) for line in data.decode().splitlines()}
    assert all(i % 2 == 0 for i in indents)


def test_export_subprocess_twice_without_env_is_byte_identical(tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "SYSTEMROOT", "TEMP", "TMP"}}
    outputs = [tmp_path / "a.json", tmp_path / "b.json"]
    for out in outputs:
        result = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "app.scripts.export_openapi", "--output", str(out)],
            cwd=API_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert result.returncode == 0, result.stderr
    assert outputs[0].read_bytes() == outputs[1].read_bytes()
    assert b"\r" not in outputs[0].read_bytes()
    # The in-process export (same code, no server, no DB) produces the same bytes.
    inproc = tmp_path / "inproc.json"
    assert main(["--output", str(inproc)]) == 0
    assert inproc.read_bytes() == outputs[0].read_bytes()


def test_check_returns_0_when_file_matches(tmp_path: Path) -> None:
    out = tmp_path / "openapi.json"
    assert main(["--output", str(out)]) == 0
    assert main(["--output", str(out), "--check"]) == 0


def test_check_returns_1_on_drift(tmp_path: Path) -> None:
    out = tmp_path / "openapi.json"
    assert main(["--output", str(out)]) == 0
    out.write_bytes(out.read_bytes().replace(b'"openapi"', b'"openapx"', 1))
    assert main(["--output", str(out), "--check"]) == 1


def test_check_returns_1_when_file_is_missing(tmp_path: Path) -> None:
    assert main(["--output", str(tmp_path / "nope.json"), "--check"]) == 1


def test_check_does_not_write_the_file(tmp_path: Path) -> None:
    out = tmp_path / "nope.json"
    main(["--output", str(out), "--check"])
    assert not out.exists()


def test_bad_arguments_exit_with_code_2() -> None:
    with pytest.raises(SystemExit) as info:
        main(["--no-such-flag"])
    assert info.value.code == 2


def test_default_output_path_is_packages_api_client_openapi_json() -> None:
    assert default_output() == REPO_ROOT / "packages" / "api-client" / "openapi.json"


def test_committed_spec_matches_the_current_app() -> None:
    assert default_output().is_file()
    assert main(["--output", str(default_output()), "--check"]) == 0


def test_root_package_script_runs_the_export_from_apps_api() -> None:
    scripts = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))["scripts"]
    assert "app.scripts.export_openapi" in scripts["openapi:export"]
    assert "apps/api" in scripts["openapi:export"]


# ------------------------------------------------- AC2: duplicates fail, naming both routes


def test_assert_unique_operation_ids_raises_naming_both_routes() -> None:
    with pytest.raises(ValueError, match="duplicate operation IDs") as info:
        assert_unique_operation_ids(_duplicate_probe())
    message = str(info.value)
    assert "cards_search" in message
    assert "GET /cards/search" in message
    assert "POST /cards/lookup" in message


def test_directly_registered_duplicates_name_both_routes() -> None:
    probe = _new_app()
    probe.add_api_route("/cards/search", _search_in_module_a(), methods=["GET"], tags=["cards"])
    probe.add_api_route("/cards/lookup", _search_in_module_b(), methods=["POST"], tags=["cards"])
    with pytest.raises(ValueError, match=r"GET /cards/search.*POST /cards/lookup"):
        assert_unique_operation_ids(probe)


def test_collect_operation_ids_lists_both_routes_under_the_shared_id() -> None:
    found = collect_operation_ids(_duplicate_probe())
    assert found["cards_search"] == ["GET /cards/search", "POST /cards/lookup"]


def test_unique_ids_pass_the_check() -> None:
    probe = _new_app()
    router = APIRouter(tags=["cards"])
    router.add_api_route("/cards/search", _search_in_module_a(), methods=["GET"])
    probe.include_router(router)
    assert_unique_operation_ids(probe)


def test_export_script_exits_3_with_both_routes_on_stderr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(app_main, "create_app", lambda *_a, **_k: _duplicate_probe())
    out = tmp_path / "openapi.json"
    assert export_openapi.main(["--output", str(out)]) == 3
    err = capsys.readouterr().err
    assert "GET /cards/search" in err
    assert "POST /cards/lookup" in err
    assert not out.exists()


def test_export_script_check_mode_also_exits_3_on_duplicates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(app_main, "create_app", lambda *_a, **_k: _duplicate_probe())
    assert export_openapi.main(["--output", str(tmp_path / "x.json"), "--check"]) == 3


def test_real_app_has_no_duplicate_operation_ids() -> None:
    assert_unique_operation_ids(app_main.create_app())


# ------------------------------------------------------------- AC2: operation ID format


def test_tagged_route_gets_tag_underscore_function() -> None:
    probe = _new_app()
    router = APIRouter(tags=["cards"])
    router.add_api_route("/cards/search", _search_in_module_a(), methods=["GET"])
    probe.include_router(router)
    assert probe.openapi()["paths"]["/cards/search"]["get"]["operationId"] == "cards_search"


def test_tag_is_slugged_and_lowercased() -> None:
    probe = _new_app()
    router = APIRouter(tags=["  PlayGroups "])
    router.add_api_route("/pg", _search_in_module_a(), methods=["GET"])
    probe.include_router(router)
    assert probe.openapi()["paths"]["/pg"]["get"]["operationId"] == "playgroups_search"


def test_first_tag_wins_when_several_are_given() -> None:
    probe = _new_app()
    router = APIRouter(tags=["decks", "cards"])
    router.add_api_route("/d", _search_in_module_a(), methods=["GET"])
    probe.include_router(router)
    assert probe.openapi()["paths"]["/d"]["get"]["operationId"] == "decks_search"


def test_untagged_route_in_domain_router_module_uses_the_package_name() -> None:
    probe = _new_app()
    endpoint = _search_in_module_a()
    endpoint.__module__ = "app.decks.router"
    probe.add_api_route("/decks/search", endpoint, methods=["GET"])
    assert route_domain(_route(probe, "/decks/search")) == "decks"
    assert probe.openapi()["paths"]["/decks/search"]["get"]["operationId"] == "decks_search"


@pytest.mark.parametrize(
    "module",
    [
        "app.core.health",
        "app.workflows.thing",
        "app.workers.settings",
        "app.scripts.export_openapi",
        "app.main",
        "tests.some_module",
        "somepkg.router",
    ],
)
def test_untagged_core_and_non_domain_routes_get_system(module: str) -> None:
    probe = _new_app()
    endpoint = _search_in_module_a()
    endpoint.__module__ = module
    probe.add_api_route("/x", endpoint, methods=["GET"])
    assert probe.openapi()["paths"]["/x"]["get"]["operationId"] == "system_search"


def test_every_operation_id_in_the_real_app_matches_the_format() -> None:
    ids = [
        op["operationId"]
        for path in app_main.create_app().openapi()["paths"].values()
        for op in path.values()
    ]
    # No domain routers have landed yet, so this is vacuous today and bites once they do.
    for op_id in ids:
        assert OPERATION_ID_RE.match(op_id), op_id
    assert len(ids) == len(set(ids))


def test_real_app_operation_id_prefixes_are_architecture_domains() -> None:
    allowed = {
        "system", "identity", "playgroups", "cards", "decks",
        "brackets", "games", "stats", "collection", "leagues",
    }  # fmt: skip
    for op_id in collect_operation_ids(app_main.create_app()):
        assert op_id.split("_", 1)[0] in allowed, op_id


# ------------------------------------------------------------- review fixes


def test_non_duplicate_value_error_during_build_does_not_exit_3(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def boom(*_a: Any, **_k: Any) -> FastAPI:
        raise ValueError("settings are wrong")

    monkeypatch.setattr(app_main, "create_app", boom)
    with pytest.raises(ValueError, match="settings are wrong"):
        export_openapi.main(["--output", str(tmp_path / "x.json")])


def test_multi_method_route_with_one_function_is_reported_as_duplicate() -> None:
    probe = _new_app()

    async def things() -> list[str]:
        return []

    probe.add_api_route("/things", things, methods=["GET", "POST"])
    with pytest.raises(DuplicateOperationIdError, match=r"GET /things and POST /things"):
        assert_unique_operation_ids(probe)


def test_domain_package_wins_over_tags() -> None:
    probe = _new_app()
    endpoint = _search_in_module_a()
    endpoint.__module__ = "app.decks.router"
    router = APIRouter(tags=["Decks UI"])
    router.add_api_route("/decks/search", endpoint, methods=["GET"])
    probe.include_router(router, tags=["admin"])
    assert probe.openapi()["paths"]["/decks/search"]["get"]["operationId"] == "decks_search"


def test_enum_tag_uses_its_value() -> None:
    class Tag(Enum):
        CARDS = "cards"

    probe = _new_app()
    router = APIRouter(tags=[Tag.CARDS])
    router.add_api_route("/c", _search_in_module_a(), methods=["GET"])
    probe.include_router(router)
    assert probe.openapi()["paths"]["/c"]["get"]["operationId"] == "cards_search"


def test_nested_include_duplicate_message_shows_full_paths() -> None:
    probe = _new_app()
    inner_a = APIRouter(tags=["cards"])
    inner_b = APIRouter(tags=["cards"])
    inner_a.add_api_route("/a", _search_in_module_a(), methods=["GET"])
    inner_b.add_api_route("/b", _search_in_module_b(), methods=["GET"])
    outer = APIRouter(prefix="/api/v1")
    outer.include_router(inner_a, prefix="/x")
    outer.include_router(inner_b, prefix="/y")
    probe.include_router(outer)
    with pytest.raises(DuplicateOperationIdError) as info:
        assert_unique_operation_ids(probe)
    assert "GET /api/v1/x/a" in str(info.value)
    assert "GET /api/v1/y/b" in str(info.value)


def test_default_output_is_resolved_lazily() -> None:
    assert default_output().name == "openapi.json"


def test_default_output_raises_when_no_workspace_marker_above(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(export_openapi, "__file__", str(tmp_path / "a" / "b.py"))
    with pytest.raises(FileNotFoundError, match=r"pnpm-workspace.yaml"):
        default_output()


def test_system_domain_package_wins_over_a_non_domain_tag() -> None:
    probe = _new_app()
    endpoint = _search_in_module_a()
    endpoint.__module__ = "app.system.router"
    router = APIRouter(tags=["health"])
    router.add_api_route("/health/live", endpoint, methods=["GET"])
    probe.include_router(router)
    assert probe.openapi()["paths"]["/health/live"]["get"]["operationId"] == "system_search"


@pytest.mark.parametrize("tag", ["!!!", "health", "Unknown Thing"])
def test_tag_that_is_not_a_domain_falls_back_to_system(tag: str) -> None:
    probe = _new_app()
    router = APIRouter(tags=[tag])
    router.add_api_route("/t", _search_in_module_a(), methods=["GET"])
    probe.include_router(router)
    assert probe.openapi()["paths"]["/t"]["get"]["operationId"] == "system_search"


def test_backstop_catches_fastapi_duplicate_warning_when_collector_finds_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.core.openapi.collect_operation_ids", lambda _app: {})
    with pytest.raises(DuplicateOperationIdError, match="Duplicate Operation ID"):
        assert_unique_operation_ids(_duplicate_probe())


def test_warning_is_not_reported_again_when_collector_found_duplicates() -> None:
    with pytest.raises(DuplicateOperationIdError) as info:
        assert_unique_operation_ids(_duplicate_probe())
    assert "Duplicate Operation ID" not in str(info.value)

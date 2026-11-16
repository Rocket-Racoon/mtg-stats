"""S-1.1.4: the prerequisite check names tool, required and found versions; exits non-zero."""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path

import check_prereqs
import pytest
from check_prereqs import (
    Requirement,
    check_prerequisites,
    load_requirements,
    main,
    parse_version,
    satisfies,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS = ["docker", "uv", "python", "node", "pnpm", "compose"]

GOOD = {
    "docker": "Docker version 27.3.1, build ce12230",
    "uv": "uv 0.10.12",
    "python": "Python 3.12.4",
    "node": "v24.19.0",
    "pnpm": "11.1.3",
    "compose": "Docker Compose version v2.35.1",
}
TOO_OLD = {
    "docker": "Docker version 24.0.7, build abc",
    "uv": "uv 0.9.5",
    "python": "Python 3.10.1",
    "node": "v20.11.0",
    "pnpm": "9.0.0",
    "compose": "Docker Compose version v2.23.3",
}
OLD_FOUND = {
    "docker": "24.0.7",
    "uv": "0.9.5",
    "python": "3.10.1",
    "node": "20.11.0",
    "pnpm": "9.0.0",
    "compose": "2.23.3",
}


def lookup_from(table: dict[str, str | None]) -> Callable[[str], str | None]:
    return table.get


def good_with(**overrides: str | None) -> dict[str, str | None]:
    table: dict[str, str | None] = dict(GOOD)
    table.update(overrides)
    return table


def reqs() -> list[Requirement]:
    return load_requirements(REPO_ROOT)


# --------------------------------------------------------------------------- parse_version


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("v24.19.0", (24, 19, 0)),
        ("Docker version 27.3.1, build ce12230", (27, 3, 1)),
        ("uv 0.9.5 (abc 2025-01-01)", (0, 9, 5)),
        ("Python 3.12.4", (3, 12, 4)),
        ("11", (11,)),
    ],
)
def test_parse_version_reads_first_dotted_number(text: str, expected: tuple[int, ...]) -> None:
    assert parse_version(text) == expected


@pytest.mark.parametrize("text", ["", "no digits here", "   "])
def test_parse_version_returns_none_without_a_number(text: str) -> None:
    assert parse_version(text) is None


# --------------------------------------------------------------------------- satisfies


@pytest.mark.parametrize(
    ("version", "spec", "expected"),
    [
        ((24, 19, 0), ">=24 <25", True),
        ((25, 0, 0), ">=24 <25", False),
        ((23, 9, 9), ">=24 <25", False),
        ((24, 0, 0), ">=24 <25", True),
        ((11, 1, 3), ">=11.1.3 <12", True),
        ((11, 1, 2), ">=11.1.3 <12", False),
        ((12, 0, 0), ">=11.1.3 <12", False),
        ((3, 12), ">=3.12", True),
        ((3, 12, 4), ">=3.12", True),
        ((3, 11, 9), ">=3.12", False),
        ((24,), ">=24.0", True),
        ((24, 0, 0), "24", True),
        ((25, 1), "24", True),
        ((23,), "24", False),
        ((24, 0), ">=24,<25", True),
        ((25, 0), ">=24,<25", False),
        ((1, 2, 3), "==1.2.3", True),
        ((1, 2, 3), "=1.2.3", True),
        ((1, 2, 4), "==1.2.3", False),
        ((1, 2, 3), "<=1.2.3", True),
        ((1, 2, 3), ">1.2.3", False),
        ((1, 2, 4), ">1.2.3", True),
        ((1, 2, 3), "<1.2.3", False),
    ],
)
def test_satisfies_evaluates_comparators(
    version: tuple[int, ...], spec: str, expected: bool
) -> None:
    assert satisfies(version, spec) is expected


@pytest.mark.parametrize("spec", ["", "   ", "~1.2", "^1.2", ">=abc", "latest"])
def test_satisfies_rejects_empty_or_unsupported_spec(spec: str) -> None:
    with pytest.raises(ValueError, match=r"version spec|comparator"):
        satisfies((1, 0, 0), spec)


# --------------------------------------------------------------------------- check_prerequisites


def test_all_prerequisites_met_gives_only_ok_findings() -> None:
    findings = check_prerequisites(reqs(), lookup_from(good_with()))
    assert [f.tool for f in findings] == TOOLS
    assert all(f.ok for f in findings)


@pytest.mark.parametrize("tool", TOOLS)
def test_missing_tool_fails_and_message_names_tool_and_not_found(tool: str) -> None:
    findings = check_prerequisites(reqs(), lookup_from(good_with(**{tool: None})))
    failed = [f for f in findings if not f.ok]
    assert [f.tool for f in failed] == [tool]
    assert f"{tool}: required " in failed[0].message
    assert "not found" in failed[0].message


@pytest.mark.parametrize("tool", TOOLS)
def test_too_old_tool_fails_and_message_names_required_and_found(tool: str) -> None:
    findings = check_prerequisites(reqs(), lookup_from(good_with(**{tool: TOO_OLD[tool]})))
    failed = [f for f in findings if not f.ok]
    assert [f.tool for f in failed] == [tool]
    message = failed[0].message
    spec = next(r.spec for r in reqs() if r.tool == tool)
    assert message.startswith(f"{tool}: required {spec}")
    assert f"found {OLD_FOUND[tool]}" in message
    assert "too old or out of range" in message


def test_node_25_is_rejected_by_the_upper_bound() -> None:
    findings = check_prerequisites(reqs(), lookup_from(good_with(node="v25.0.0")))
    node = next(f for f in findings if f.tool == "node")
    assert not node.ok
    assert "found 25.0.0" in node.message


def test_pnpm_12_is_rejected_by_the_upper_bound() -> None:
    findings = check_prerequisites(reqs(), lookup_from(good_with(pnpm="12.0.0")))
    pnpm = next(f for f in findings if f.tool == "pnpm")
    assert not pnpm.ok
    assert "found 12.0.0" in pnpm.message


@pytest.mark.parametrize("tool", TOOLS)
def test_unparseable_output_fails_and_quotes_the_raw_text(tool: str) -> None:
    findings = check_prerequisites(reqs(), lookup_from(good_with(**{tool: "garbage output"})))
    failed = [f for f in findings if not f.ok]
    assert [f.tool for f in failed] == [tool]
    assert "version not recognised in 'garbage output'" in failed[0].message


def test_every_failing_tool_is_reported_not_just_the_first() -> None:
    findings = check_prerequisites(reqs(), lookup_from({}))
    assert [f.tool for f in findings] == TOOLS
    assert not any(f.ok for f in findings)


def test_ok_message_shows_the_found_version() -> None:
    findings = check_prerequisites(reqs(), lookup_from(good_with()))
    node = next(f for f in findings if f.tool == "node")
    assert "node: required " in node.message
    assert "found 24.19.0" in node.message


# --------------------------------------------------------------------------- load_requirements


def test_load_requirements_orders_the_six_tools() -> None:
    assert [r.tool for r in reqs()] == TOOLS


def test_python_requirement_comes_from_pyproject_requires_python() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "apps/api/pyproject.toml").read_text(encoding="utf-8"))
    spec = pyproject["project"]["requires-python"]
    assert next(r for r in reqs() if r.tool == "python").spec == spec


def test_node_and_pnpm_requirements_come_from_package_json_engines() -> None:
    engines = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))["engines"]
    by_tool = {r.tool: r.spec for r in reqs()}
    assert by_tool["node"] == engines["node"]
    assert by_tool["pnpm"] == engines["pnpm"]


def test_load_requirements_follows_a_changed_pin(tmp_path: Path) -> None:
    (tmp_path / "apps/api").mkdir(parents=True)
    (tmp_path / "apps/api/pyproject.toml").write_text(
        '[project]\nname = "x"\nrequires-python = ">=3.13"\n', encoding="utf-8"
    )
    (tmp_path / "package.json").write_text(
        json.dumps({"engines": {"node": ">=26 <27", "pnpm": ">=12 <13"}}), encoding="utf-8"
    )
    by_tool = {r.tool: r.spec for r in load_requirements(tmp_path)}
    assert by_tool["python"] == ">=3.13"
    assert by_tool["node"] == ">=26 <27"
    assert by_tool["pnpm"] == ">=12 <13"


# --------------------------------------------------------------------------- main


def test_main_exits_zero_and_prints_each_tool_when_all_met(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main([], root=REPO_ROOT, lookup=lookup_from(good_with()))
    out = capsys.readouterr().out
    assert code == 0
    for tool in TOOLS:
        assert f"{tool}: required " in out
    assert len([line for line in out.splitlines() if line.strip()]) >= 6


@pytest.mark.parametrize("tool", TOOLS)
def test_main_exits_one_when_a_tool_is_missing(
    tool: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main([], root=REPO_ROOT, lookup=lookup_from(good_with(**{tool: None})))
    out = capsys.readouterr().out
    assert code == 1
    assert f"{tool}: required " in out
    assert "not found" in out


@pytest.mark.parametrize("tool", TOOLS)
def test_main_exits_one_and_names_required_and_found_when_too_old(
    tool: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main([], root=REPO_ROOT, lookup=lookup_from(good_with(**{tool: TOO_OLD[tool]})))
    out = capsys.readouterr().out
    spec = next(r.spec for r in reqs() if r.tool == tool)
    assert code == 1
    assert f"{tool}: required {spec}" in out
    assert f"found {OLD_FOUND[tool]}" in out


def test_main_exits_one_for_node_25(capsys: pytest.CaptureFixture[str]) -> None:
    code = main([], root=REPO_ROOT, lookup=lookup_from(good_with(node="v25.0.0")))
    assert code == 1
    assert "found 25.0.0" in capsys.readouterr().out


# --------------------------------------------------------------------------- stdlib only


def test_check_prereqs_imports_only_the_standard_library() -> None:
    tree = ast.parse(Path(check_prereqs.__file__).read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module.split(".")[0])
    assert modules, "expected at least one import"
    assert modules <= set(sys.stdlib_module_names)


def test_docker_uv_and_compose_floors_are_25_0_0_10_and_2_24() -> None:
    by_tool = {r.tool: r.spec for r in reqs()}
    assert by_tool["docker"] == ">=25.0"
    assert by_tool["uv"] == ">=0.10"
    assert by_tool["compose"] == ">=2.24"


@pytest.mark.parametrize(
    ("tool", "answer", "ok"),
    [
        ("docker", "Docker version 25.0.0, build x", True),
        ("docker", "Docker version 24.0.9, build x", False),
        ("uv", "uv 0.10.0", True),
        ("uv", "uv 0.9.30", False),
        ("compose", "Docker Compose version v2.24.0", True),
        ("compose", "Docker Compose version v2.23.9", False),
        ("compose", "Docker Compose version v5.0.0", True),
    ],
)
def test_floor_boundaries(tool: str, answer: str, ok: bool) -> None:
    findings = check_prerequisites(reqs(), lookup_from(good_with(**{tool: answer})))
    assert next(f for f in findings if f.tool == tool).ok is ok


# --------------------------------------------------------------------------- old interpreters (AC2)


def _module_tree() -> ast.Module:
    source = Path(check_prereqs.__file__).read_text(encoding="utf-8")
    # Rejects syntax newer than 3.9 (match statements, parenthesised context managers...).
    return ast.parse(source, feature_version=(3, 9))


def test_no_top_level_tomllib_import() -> None:
    for node in _module_tree().body:
        if isinstance(node, ast.Import):
            assert all(alias.name.split(".")[0] != "tomllib" for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] != "tomllib"


def test_annotations_are_deferred_so_union_syntax_is_safe_on_old_pythons() -> None:
    body = _module_tree().body
    assert any(
        isinstance(n, ast.ImportFrom)
        and n.module == "__future__"
        and any(a.name == "annotations" for a in n.names)
        for n in body
    )


def test_no_runtime_evaluated_pipe_union_at_module_level() -> None:
    """Module-level assignments are evaluated at import: ``X | None`` there breaks Python 3.9."""
    for node in _module_tree().body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            for sub in ast.walk(node.value):
                assert not (isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.BitOr))


# ------------------------------------------------------------------- getting-started guide (AC3)

GUIDE = REPO_ROOT / "docs/getting-started.md"
PNPM_BUILTINS = {
    "install", "i", "add", "remove", "update", "exec", "run", "dlx", "test", "start", "store",
    "audit", "outdated", "list", "why", "rebuild", "prune", "dedupe", "env", "setup", "init",
    "create", "approve-builds",
}  # fmt: skip


PNPM_CALL = re.compile(r"(?<![\w@/-])pnpm\s+(?:(--[\w-]+)\s+\S+\s+)?([\w:.-]+)")


def _fences(language: str | None = None) -> list[str]:
    text = GUIDE.read_text(encoding="utf-8")
    blocks = re.findall(r"^```(\w*)\n(.*?)^```", text, re.M | re.S)
    return [body for lang, body in blocks if language is None or lang == language]


def _root_scripts() -> set[str]:
    return set(json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))["scripts"])


def test_every_pnpm_command_in_the_guide_is_a_root_script_or_a_pnpm_builtin() -> None:
    scripts = _root_scripts()
    found: list[str] = []
    for body in _fences():
        for line in body.splitlines():
            for match in PNPM_CALL.finditer(line):
                if match.group(1) is not None:  # --filter <pkg> <command>
                    continue
                found.append(match.group(2))
                name = match.group(2)
                assert name in scripts or name in PNPM_BUILTINS or name.startswith("-"), line
    assert {"prereqs", "install"} <= set(found)


_BASH_ONLY = [
    (r"&&", "&&"),
    (r"\|\|", "||"),
    (r"(?m)^\s*export\s", "export"),
    (r"(?m)^\s*[A-Z_][A-Z0-9_]*=\S+\s+\S", "VAR=value cmd"),
    (r"\$\(", "$("),
    (r"(?m)^\s*cp\s", "cp"),
    (r"(?m)^\s*rm\s+-", "rm -"),
    (r"(?m)\\[ \t]*$", "trailing backslash"),
]


@pytest.mark.parametrize(("pattern", "label"), _BASH_ONLY, ids=[b[1] for b in _BASH_ONLY])
def test_powershell_blocks_contain_no_bash_only_syntax(pattern: str, label: str) -> None:
    blocks = _fences("powershell")
    assert blocks, "the guide must have powershell blocks"
    for body in blocks:
        assert re.search(pattern, body) is None, f"{label} in powershell block:\n{body}"


def _slug(heading: str) -> str:
    text = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return text.replace(" ", "-")


def test_readme_anchor_to_the_guide_matches_a_real_heading() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    anchors = re.findall(r"docs/getting-started\.md#([\w-]+)", readme)
    assert anchors, "README should deep-link into the guide"
    text = re.sub(r"^```.*?^```", "", GUIDE.read_text(encoding="utf-8"), flags=re.M | re.S)
    slugs = {_slug(m) for m in re.findall(r"^#{1,6}\s+(.+)$", text, re.M)}
    for anchor in anchors:
        assert anchor in slugs


def test_readme_links_to_the_guide() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/getting-started.md" in readme


@pytest.mark.parametrize("script", ["dev:api", "dev:web", "dev:mobile", "dev:worker"])
def test_guide_mentions_each_dev_script_and_it_exists(script: str) -> None:
    assert f"pnpm {script}" in GUIDE.read_text(encoding="utf-8")
    assert script in _root_scripts()


# ------------------------------------------------------------------- probe_tool (real lookup)


class _Completed:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _patch_probe(
    monkeypatch: pytest.MonkeyPatch,
    *,
    which: str | None = "/bin/tool",
    run: Callable[..., object] | None = None,
) -> list[list[str]]:
    """Patch shutil.which and subprocess.run; return the argv lists passed to run."""
    calls: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return which

    def fake_run(argv: list[str], **kwargs: object) -> object:
        calls.append(list(argv))
        if run is not None:
            return run(argv, **kwargs)
        return _Completed(stdout="tool 1.2.3\n")

    monkeypatch.setattr(shutil, "which", fake_which)
    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


@pytest.mark.parametrize("tool", ["docker", "uv", "node", "pnpm", "compose"])
def test_probe_returns_none_when_the_executable_is_not_found(
    tool: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _patch_probe(monkeypatch, which=None)
    assert check_prereqs.probe_tool(tool) is None
    assert calls == []


def test_probe_returns_none_on_non_zero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_probe(monkeypatch, run=lambda argv, **kw: _Completed(1, "uv 0.10.12", ""))
    assert check_prereqs.probe_tool("uv") is None


def test_probe_returns_none_on_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(argv: list[str], **kwargs: object) -> object:
        raise OSError("cannot execute")

    _patch_probe(monkeypatch, run=boom)
    assert check_prereqs.probe_tool("node") is None


def test_probe_returns_none_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def slow(argv: list[str], **kwargs: object) -> object:
        raise subprocess.TimeoutExpired(cmd=argv, timeout=60)

    _patch_probe(monkeypatch, run=slow)
    assert check_prereqs.probe_tool("pnpm") is None


def test_probe_returns_stripped_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_probe(monkeypatch, run=lambda argv, **kw: _Completed(0, "  v24.19.0\n", "noise"))
    assert check_prereqs.probe_tool("node") == "v24.19.0"


def test_probe_falls_back_to_stderr_when_stdout_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_probe(monkeypatch, run=lambda argv, **kw: _Completed(0, "", "  Docker version 27.3.1\n"))
    assert check_prereqs.probe_tool("docker") == "Docker version 27.3.1"


def test_probe_python_reports_the_running_interpreter_without_spawning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_probe(monkeypatch, which=None)
    assert check_prereqs.probe_tool("python") == ".".join(map(str, sys.version_info[:3]))
    assert calls == []


def test_probe_compose_runs_docker_compose_version(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_probe(monkeypatch, which="/resolved/docker")
    check_prereqs.probe_tool("compose")
    assert calls == [["/resolved/docker", "compose", "version"]]


@pytest.mark.parametrize("tool", ["docker", "uv", "node", "pnpm"])
def test_probe_other_tools_run_version_flag(tool: str, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_probe(monkeypatch, which=f"/resolved/{tool}")
    check_prereqs.probe_tool(tool)
    assert calls == [[f"/resolved/{tool}", "--version"]]


# ------------------------------------------------------------------- JWT_SECRET in the guide


def _guide_text() -> str:
    return GUIDE.read_text(encoding="utf-8")


def test_env_example_has_a_jwt_secret() -> None:
    env = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"(?m)^JWT_SECRET=", env)


def test_guide_tells_the_reader_to_generate_a_jwt_secret() -> None:
    text = _guide_text()
    assert "JWT_SECRET" in text
    assert "secrets.token_urlsafe" in text


def test_jwt_secret_generation_comes_before_the_first_command_that_starts_the_api() -> None:
    text = _guide_text()
    generate = text.index("secrets.token_urlsafe")
    starts = [m.start() for m in re.finditer(r"docker compose[^\n]*\bup\b|pnpm dev:api\b", text)]
    assert starts, "the guide must show how to start the API or the Compose stack"
    assert generate < min(starts)


def test_guide_does_not_claim_every_env_example_value_is_a_safe_default() -> None:
    text = _guide_text()
    assert (
        re.search(r"\b(every|all)\s+(value|setting|variable)s?\b[^.\n]*\bsafe\b", text, re.I)
        is None
    )


# ------------------------------------------------------------------- Callable import (AC2)


def test_callable_is_imported_from_typing_not_collections_abc() -> None:
    """Lookup subscripts ``Callable`` at import time; only typing's works on Python 3.8."""
    imported_from: dict[str, str] = {}
    for node in _module_tree().body:
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                imported_from[alias.asname or alias.name] = node.module
    assert imported_from.get("Callable") == "typing"


def test_no_module_level_runtime_subscript_of_collections_abc_types() -> None:
    abc_names = {
        alias.asname or alias.name
        for node in _module_tree().body
        if isinstance(node, ast.ImportFrom) and node.module == "collections.abc"
        for alias in node.names
    }
    for node in _module_tree().body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            for sub in ast.walk(node.value):
                if isinstance(sub, ast.Subscript) and isinstance(sub.value, ast.Name):
                    assert sub.value.id not in abc_names

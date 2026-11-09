"""Tests for the pre-commit config (S-1.8.7), from the acceptance criteria.

The YAML is parsed minimally (pyyaml is not a declared dependency), and
``pre-commit validate-config`` is the authority on schema validity.

Manual only (not automatable here): AC1 real installation on Windows PowerShell, macOS and Linux;
AC2 timing (15 s on a typical commit, machine dependent); AC4 (CI secrets check, S-1.8.2).
"""

# ruff: noqa: S603, S607

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / ".pre-commit-config.yaml"
API_DIR = REPO_ROOT / "apps" / "api"
SCRIPT = REPO_ROOT / "scripts" / "secrets_check.py"

HAS_GIT = shutil.which("git") is not None
HAS_UV = shutil.which("uv") is not None
requires_git_uv = pytest.mark.skipif(not (HAS_GIT and HAS_UV), reason="git and uv are required")

# Built at runtime so this file never trips the secrets check itself.
LITERAL = "zq9" + "Xv7wLmN3pQ" + "r5Ts"

Repo = dict[str, object]


def parse_config() -> list[Repo]:
    """Return ``[{repo, rev, hooks: {id: {key: raw value}}}]`` from the simple, flat config."""
    repos: list[Repo] = []
    hook: dict[str, str] | None = None
    for raw in CONFIG.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        line = raw.split(" #")[0].rstrip()
        if m := re.match(r"\s*- repo:\s*(\S+)", line):
            repos.append({"repo": m.group(1), "rev": None, "hooks": {}})
            hook = None
        elif m := re.match(r"\s*- id:\s*(\S+)", line):
            hook = {}
            hooks = repos[-1]["hooks"]
            assert isinstance(hooks, dict)
            hooks[m.group(1)] = hook
        elif m := re.match(r"\s+(\w+):\s*(.*)$", line):
            key, value = m.group(1), m.group(2).strip().strip("'\"")
            if key == "rev" and hook is None:
                repos[-1]["rev"] = value
            elif hook is not None:
                hook[key] = value
    return repos


def all_hooks() -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for repo in parse_config():
        hooks = repo["hooks"]
        assert isinstance(hooks, dict)
        result.update(hooks)
    return result


def test_config_passes_pre_commit_validate_config() -> None:
    if not HAS_UV:
        pytest.skip("uv is not installed")
    proc = subprocess.run(
        ["uv", "run", "--project", str(API_DIR), "pre-commit", "validate-config", str(CONFIG)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_secrets_check_hook_is_local_staged_always_run_without_filenames() -> None:
    local = next(r for r in parse_config() if r["repo"] == "local")
    hooks = local["hooks"]
    assert isinstance(hooks, dict)
    hook = hooks["secrets-check"]
    assert "scripts/secrets_check.py --staged" in hook["entry"]
    assert hook["always_run"] == "true"
    assert hook["pass_filenames"] == "false"


def test_detect_private_key_hook_is_configured() -> None:
    assert "detect-private-key" in all_hooks()


def test_large_file_hook_limits_to_500_kb() -> None:
    assert "--maxkb=500" in all_hooks()["check-added-large-files"]["args"]


def test_end_of_file_fixer_hook_is_configured() -> None:
    assert "end-of-file-fixer" in all_hooks()


def test_ruff_check_runs_with_fix_on_python_files_only() -> None:
    hook = all_hooks()["ruff-check"]
    assert "ruff check" in hook["entry"]
    assert "--fix" in hook["entry"]
    assert hook["types"] == "[python]"


def test_ruff_format_runs_on_python_files_only() -> None:
    hook = all_hooks()["ruff-format"]
    assert "ruff format" in hook["entry"]
    assert "--check" not in hook["entry"]
    assert hook["types"] == "[python]"


def test_prettier_is_a_local_hook_scoped_to_ts_and_js_files() -> None:
    hook = all_hooks()["prettier"]
    assert "prettier" in hook["entry"]
    for ext in ("ts", "tsx", "js"):
        assert re.search(hook["files"], f"apps/web/src/a.{ext}"), ext
    assert not re.search(hook["files"], "apps/api/app/a.py")


def test_eslint_is_a_local_hook_scoped_to_ts_and_js_files() -> None:
    hook = all_hooks()["eslint"]
    assert "eslint" in hook["entry"]
    for ext in ("ts", "tsx", "js"):
        assert re.search(hook["files"], f"apps/web/src/a.{ext}"), ext
    assert not re.search(hook["files"], "apps/api/app/a.py")
    assert not re.search(hook["files"], "README.md")


def test_prettier_and_eslint_are_local_hooks() -> None:
    local = next(r for r in parse_config() if r["repo"] == "local")
    hooks = local["hooks"]
    assert isinstance(hooks, dict)
    assert {"prettier", "eslint"} <= set(hooks)


def test_every_remote_repo_pins_an_exact_tag_or_sha() -> None:
    remotes = [r for r in parse_config() if r["repo"] not in ("local", "meta")]
    assert remotes, "expected at least one remote repo"
    for r in remotes:
        rev = str(r["rev"])
        assert rev not in {"main", "master", "HEAD", "latest", "develop", ""}, r["repo"]
        is_tag = re.fullmatch(r"v?\d+\.\d+(\.\d+)*", rev)
        is_sha = re.fullmatch(r"[0-9a-f]{40}", rev)
        assert is_tag or is_sha, f"{r['repo']} rev {rev!r} is not an exact tag or SHA"


def test_docs_backlog_is_excluded_from_rewriting_hooks() -> None:
    hooks = all_hooks()
    for hook_id in ("end-of-file-fixer", "prettier", "eslint"):
        exclude = hooks[hook_id]["exclude"]
        assert re.search(exclude, "docs/backlog/S-1.md"), hook_id
        assert not re.search(exclude, "apps/web/src/a.ts"), hook_id


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)


def _stage(path: Path, name: str, content: str) -> None:
    (path / name).write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "--", name], cwd=path, check=True)


def _run_hook(path: Path) -> subprocess.CompletedProcess[str]:
    """Run the hook's own entry command with the temp repo as cwd."""
    return subprocess.run(
        ["uv", "run", "--project", str(API_DIR), "python", str(SCRIPT), "--staged"],
        cwd=path,
        capture_output=True,
        text=True,
        check=False,
    )


@requires_git_uv
def test_staged_env_file_refuses_the_commit_and_names_the_file(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _stage(tmp_path, ".env.local", f"DATABASE_PASSWORD={LITERAL}\n")
    proc = _run_hook(tmp_path)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, out
    assert ".env.local" in out
    assert LITERAL not in out


@requires_git_uv
def test_staged_secret_line_refuses_the_commit_names_path_and_line_without_the_value(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    _stage(tmp_path, "settings.py", f'import os\n\nAPI_TOKEN = "{LITERAL}"\n')
    proc = _run_hook(tmp_path)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, out
    assert "settings.py:3" in out
    assert LITERAL not in out


@requires_git_uv
def test_clean_staged_file_passes_the_secrets_hook(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _stage(tmp_path, "notes.txt", "nothing secret here\n")
    proc = _run_hook(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_package_json_has_hooks_install_script() -> None:
    scripts = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))["scripts"]
    assert "pre-commit install" in scripts["hooks:install"]
    assert "--project apps/api" in scripts["hooks:install"]


def test_readme_documents_install_command_and_never_no_verify_rule() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "hooks:install" in readme or "uv run --project apps/api pre-commit install" in readme
    assert re.search(r"never[^.\n]*--no-verify", readme, re.IGNORECASE)


@pytest.mark.skipif(
    os.environ.get("RUN_PRECOMMIT_ALL") != "1", reason="slow; set RUN_PRECOMMIT_ALL=1 to run"
)
def test_pre_commit_run_all_files_exits_zero() -> None:
    start = time.monotonic()
    proc = subprocess.run(
        ["uv", "run", "--project", "apps/api", "pre-commit", "run", "--all-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    elapsed = time.monotonic() - start
    assert proc.returncode == 0, f"{elapsed:.1f}s " + proc.stdout[-3000:] + proc.stderr[-1000:]

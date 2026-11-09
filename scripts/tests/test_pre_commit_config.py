"""Tests for the pre-commit config (S-1.8.7), from the acceptance criteria.

The YAML is parsed minimally (pyyaml is not a declared dependency), and
``pre-commit validate-config`` is the authority on schema validity.

Coverage notes:
- AC1 (fresh clone plus ``pnpm hooks:install`` runs the hooks on commit) is covered by the opt-in
  end-to-end test (``RUN_PRECOMMIT_E2E=1``) on the current OS. macOS and Linux remain manual.
- AC2 timing is measured at 13.8 s warm, accepted by the user; it is not asserted here.
- AC4 (CI re-runs the secrets check) is deferred to S-1.8.2.
"""

# ruff: noqa: S603, S607

from __future__ import annotations

import json
import os
import re
import shlex
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

E2E_ENABLED = os.environ.get("RUN_PRECOMMIT_E2E") == "1"

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


def test_ruff_check_runs_with_fix_concise_output_on_python_files_only() -> None:
    hook = all_hooks()["ruff-check"]
    assert "ruff check" in hook["entry"]
    assert "--fix" in hook["entry"]
    assert "--output-format=concise" in hook["entry"]
    assert "--config" not in hook["entry"]
    assert "python" in hook["types"]


def test_ruff_format_runs_on_python_files_only_without_a_config_override() -> None:
    hook = all_hooks()["ruff-format"]
    assert "ruff format" in hook["entry"]
    assert "--check" not in hook["entry"]
    assert "--config" not in hook["entry"]
    assert "python" in hook["types"]


def test_scripts_ruff_config_extends_the_api_config() -> None:
    text = (REPO_ROOT / "scripts" / "ruff.toml").read_text(encoding="utf-8")
    assert re.search(r'^extend\s*=\s*"[^"]*apps/api/pyproject\.toml"', text, re.MULTILINE)


def test_secrets_check_runs_first_and_the_config_fails_fast() -> None:
    first_repo = parse_config()[0]
    hooks = first_repo["hooks"]
    assert isinstance(hooks, dict)
    assert first_repo["repo"] == "local"
    assert next(iter(hooks)) == "secrets-check"
    assert hooks["secrets-check"]["fail_fast"] is True
    assert not re.search(r"^fail_fast:", CONFIG.read_text(encoding="utf-8"), re.MULTILINE)


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


def _secrets_command() -> list[str]:
    """The secrets hook's own entry from the config, with repo-relative paths made absolute."""
    entry = all_hooks()["secrets-check"]["entry"]
    mapping = {"apps/api": str(API_DIR), "scripts/secrets_check.py": str(SCRIPT)}
    return [mapping.get(token, token) for token in shlex.split(entry)]


def test_secrets_hook_entry_is_staged_mode_and_resolves_to_real_paths() -> None:
    command = _secrets_command()
    assert "--staged" in command
    assert str(SCRIPT) in command
    assert str(API_DIR) in command


def _run_hook(path: Path) -> subprocess.CompletedProcess[str]:
    """Run the hook's own entry command (derived from the config) with the temp repo as cwd."""
    return subprocess.run(
        _secrets_command(),
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
def test_staged_literal_env_file_with_harmless_content_is_refused_by_name(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _stage(tmp_path, ".env", "DEBUG=1\n")
    proc = _run_hook(tmp_path)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, out
    assert ".env" in out
    assert "tracked-env-file" in out


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


def _git(clone: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=clone,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.skipif(not E2E_ENABLED, reason="slow; set RUN_PRECOMMIT_E2E=1 to run")
def test_fresh_clone_with_documented_setup_enforces_hooks_on_commit(tmp_path: Path) -> None:
    """AC1 and AC3 end to end: clone, ``pnpm hooks:install``, then real ``git commit`` runs."""
    clone = tmp_path / "clone"
    cloned = subprocess.run(
        ["git", "clone", "--no-hardlinks", "-q", str(REPO_ROOT), str(clone)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert cloned.returncode == 0, cloned.stderr
    pnpm = "pnpm.cmd" if os.name == "nt" else "pnpm"
    env = {**os.environ, "CI": "true"}
    deps = subprocess.run(
        [pnpm, "install", "--frozen-lockfile"],
        cwd=clone,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert deps.returncode == 0, deps.stdout[-2000:] + deps.stderr[-1000:]
    installed = subprocess.run(
        [pnpm, "hooks:install"], cwd=clone, capture_output=True, text=True, check=False
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr
    assert (clone / ".git" / "hooks" / "pre-commit").exists()

    # (a) a literal .env is refused and named.
    (clone / ".env").write_text("DEBUG=1\n", encoding="utf-8")
    _git(clone, "add", "-f", ".env")  # .env is gitignored
    refused_env = _git(clone, "commit", "-m", "add env")
    env_out = refused_env.stdout + refused_env.stderr
    assert refused_env.returncode != 0, env_out
    assert ".env" in env_out
    assert "tracked-env-file" in env_out
    _git(clone, "reset", "-q")
    (clone / ".env").unlink()

    # (b) a secret line is refused, path:line is named, the literal is printed nowhere.
    leak = clone / "apps" / "api" / "leak.py"
    leak.write_text(f'"""Doc."""\n\nAPI_TOKEN = "{LITERAL}"\n', encoding="utf-8")
    _git(clone, "add", "apps/api/leak.py")
    refused_secret = _git(clone, "commit", "-m", "add secret")
    secret_out = refused_secret.stdout + refused_secret.stderr
    assert refused_secret.returncode != 0, secret_out
    assert "apps/api/leak.py:3" in secret_out.replace("\\", "/")
    assert LITERAL not in secret_out
    _git(clone, "reset", "-q")
    leak.unlink()

    # (c) a clean change commits.
    (clone / "NOTES.txt").write_text("clean change\n", encoding="utf-8")
    _git(clone, "add", "NOTES.txt")
    start = time.monotonic()
    ok = _git(clone, "commit", "-m", "clean change")
    elapsed = time.monotonic() - start
    assert ok.returncode == 0, f"{elapsed:.1f}s " + ok.stdout + ok.stderr

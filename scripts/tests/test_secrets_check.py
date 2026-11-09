"""Tests for the secrets check and ``.env.example`` (S-1.3.3), from the acceptance criteria.

Pattern cases are pure ``scan_text`` unit tests (no git). The end-to-end default and staged runs
share one module-scoped temp repo that is built and scanned once per mode. Separate repos exist
only where a test needs a distinct index and working-tree state.
"""

# ruff: noqa: S101, S603, S607

from __future__ import annotations

import contextlib
import io
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
import secrets_check

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = REPO_ROOT / ".env.example"
SCRIPT = REPO_ROOT / "scripts" / "secrets_check.py"

# Built by concatenation so this file never trips the check itself.
KEY_HEADER = "-----BEGIN " + "RSA PRIVATE" + " KEY-----"
SECRET_VALUE = "zq9" + "Xv7wLmN3pQ"
SENSITIVE_SUFFIXES = ["SECRET", "KEY", "TOKEN", "PASSWORD"]

HAS_GIT = shutil.which("git") is not None
requires_git = pytest.mark.skipif(not HAS_GIT, reason="git is not installed")

GIT_IDENTITY = [
    "-c",
    "user.name=t",
    "-c",
    "user.email=t@example.invalid",
    "-c",
    "commit.gpgsign=false",
]


def git(repo: Path, *args: str, stdin: bytes | None = None) -> bytes:
    result = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, input=stdin)
    return result.stdout


def commit(repo: Path, message: str = "c") -> None:
    git(repo, *GIT_IDENTITY, "commit", "-q", "-m", message)


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    return path


def write(repo: Path, rel: str, text: str | bytes, *, add: bool = True) -> None:
    file = repo / rel
    file.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        file.write_bytes(text)
    else:
        file.write_text(text, encoding="utf-8", newline="")
    if add:
        git(repo, "add", "--", rel)


@dataclass(frozen=True)
class Result:
    code: int
    out: str
    err: str

    @property
    def lines(self) -> list[str]:
        return self.out.splitlines()


def run_main(repo: Path, *, staged: bool = False) -> Result:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = secrets_check.main(["--staged"] if staged else [], repo=repo)
    return Result(code, out.getvalue(), err.getvalue())


def rules(path: str, text: str) -> list[tuple[int, str]]:
    return [(f.line, f.rule) for f in secrets_check.scan_text(path, text)]


# ------------------------------------------------------------ scan_text: AC3 positives

# (file path, line template). ``{n}`` is the variable name, ``{v}`` the secret literal.
ASSIGNMENT_FORMS = [
    (".env", "{n}={v}"),
    (".env", "{n}='{v}'"),
    ("config/app.yaml", "{n}: {v}"),
    ("config/app.yml", '{n}: "{v}"'),
    ("app/settings.py", '{n} = "{v}"'),
    ("app/settings.py", '{n}: str = "{v}"'),
    ("app/settings.py", "CONFIG['{n}'] = '{v}'"),
    ("app/settings.py", '{n} = f"{v}"'),
    ("app/settings.py", '{n} = b"{v}"'),
    ("app/settings.py", '{n} = r"{v}"'),
    ("app/settings.py", '{n} = rb"{v}"'),
    ("web/src/config.ts", 'const {n} = "{v}";'),
    ("web/src/config.ts", '  "{n}": "{v}",'),
    ("web/src/config.mts", "const {n} = '{v}'"),
    ("web/src/config.cts", "const {n} = '{v}'"),
    ("config.json", '  "{n}": "{v}",'),
    ("config.json", '{{"{n}": "{v}"}}'),
    ("scripts/run.sh", "export {n}={v}"),
    ("Dockerfile", "ENV {n}={v}"),
    ("README.md", "{n}={v}"),
    ("notes.txt", "{n}={v}"),
    ("setup.ps1", "$env:{n}='{v}'"),
]


@pytest.mark.parametrize("suffix", SENSITIVE_SUFFIXES)
@pytest.mark.parametrize(("path", "template"), ASSIGNMENT_FORMS)
def test_secret_assignment_is_found_on_the_right_line(
    suffix: str, path: str, template: str
) -> None:
    line = template.format(n=f"JWT_{suffix}", v=SECRET_VALUE)
    assert rules(path, f"# header\n\n{line}\n") == [(3, "secret-assignment")]


def test_private_key_block_is_found_on_the_right_line() -> None:
    assert rules("keys/id.txt", f"hello\n{KEY_HEADER}\nabcd\n") == [(2, "private-key")]


@pytest.mark.parametrize("kind", ["EC ", "OPENSSH ", "ENCRYPTED ", ""])
def test_private_key_variants_are_found(kind: str) -> None:
    assert rules("k.pem", "-----BEGIN " + kind + "PRIVATE" + " KEY-----\n") == [(1, "private-key")]


@pytest.mark.parametrize("scheme", ["postgresql+psycopg", "redis", "https"])
def test_connection_string_with_inline_password_is_found(scheme: str) -> None:
    url = f"{scheme}://app:{SECRET_VALUE}@db.internal:5432/mtg"
    assert rules("docs/setup.md", f"intro\n\nuse {url} here\n") == [(3, "inline-password")]


@pytest.mark.parametrize("scheme", ["redis", "postgres"])
def test_connection_string_with_empty_user_is_found(scheme: str) -> None:
    assert rules("a.md", f"{scheme}://:{SECRET_VALUE}@host\n") == [(1, "inline-password")]


def test_every_match_on_a_line_is_checked_for_assignments() -> None:
    line = f"A_KEY=CHANGE_ME B_TOKEN={SECRET_VALUE}\n"
    assert rules("a.env", line) == [(1, "secret-assignment")]


def test_every_url_on_a_line_is_checked() -> None:
    line = f"x://u:CHANGE_ME@h and redis://:{SECRET_VALUE}@h\n"
    assert rules("a.md", line) == [(1, "inline-password")]


def test_two_rules_on_one_line_are_both_reported() -> None:
    line = f"{KEY_HEADER} A_KEY={SECRET_VALUE}\n"
    assert rules("a.env", line) == [(1, "private-key"), (1, "secret-assignment")]


def test_form_feed_does_not_shift_line_numbers() -> None:
    text = f"one\ftwo\nthree\f\nA_KEY={SECRET_VALUE}\n"
    assert rules("a.env", text) == [(3, "secret-assignment")]


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_line_numbers_are_the_same_for_lf_and_crlf(newline: str) -> None:
    text = newline.join(["a", "b", f"A_KEY={SECRET_VALUE}"]) + newline
    assert rules("a.env", text) == [(3, "secret-assignment")]


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-le-bom", "utf-8-sig"])
def test_bom_files_are_decoded_and_scanned(encoding: str) -> None:
    text = f"x\nA_KEY={SECRET_VALUE}\n"
    data = (
        b"\xff\xfe" + text.encode("utf-16-le")
        if encoding == "utf-16-le-bom"
        else text.encode(encoding)
    )
    decoded = secrets_check.decode_text(data)
    assert decoded is not None
    assert rules("a.env", decoded) == [(2, "secret-assignment")]


def test_binary_data_decodes_to_none() -> None:
    assert secrets_check.decode_text(b"abc\0def") is None


# ------------------------------------------------------------ scan_text: AC3 negatives

NEGATIVE_LINES = [
    ("a.env", "{n}=CHANGE_ME"),
    ("a.env", "{n}="),
    ("a.env", "{n}=${{SOME_VAR}}"),
    ("a.yaml", "{n}: CHANGE_ME"),
    ("a.yaml", '{n}: ""'),
    ("a.yaml", "{n}: ${{{{ secrets.OTHER }}}}"),
    ("a.py", '{n} = "CHANGE_ME"'),
    ("a.py", '{n} = ""'),
    ("a.py", "x = settings.{n}"),
    ("a.py", "{n} = settings.{n}"),
    ("a.py", 'token = os.environ["{n}"]'),
    ("a.py", 'if os.environ["{n}"] == "abc":'),
    ("a.py", 'if {n} == "abc":'),
    ("a.py", 'x = "{n}="'),
    ("a.ts", "const k = process.env.{n};"),
    ("a.ts", 'if ({n} === "abc") {{}}'),
    ("a.ts", 'const t = import.meta.env["VITE_{n}"];'),
    ("a.json", '{{"{n}": "CHANGE_ME"}}'),
    ("a.md", "Set {n} to your own value."),
    ("a.md", "Set {n}=value before starting."),
    ("a.md", "{n}=CHANGE_ME"),
    ("a.md", "{n}=${{OTHER}}"),
    ("run.sh", "export {n}=CHANGE_ME"),
    ("run.sh", "export {n}=${{OTHER}}"),
    ("Dockerfile", "ENV {n}=CHANGE_ME"),
    ("Dockerfile", "ENV {n}=${{OTHER}}"),
    ("a.txt", "{n}=${{OTHER}}"),
    ("setup.ps1", "$env:{n}=$other"),
    ("setup.ps1", "$env:{n}='CHANGE_ME'"),
]


@pytest.mark.parametrize(("path", "template"), NEGATIVE_LINES)
def test_placeholders_references_and_prose_are_clean(path: str, template: str) -> None:
    assert rules(path, template.format(n="JWT_SECRET") + "\n") == []


LITERAL_NEGATIVES = [
    ("a.md", "    jwt_secret: SecretStr"),
    ("a.md", '    jwt_secret: str = ""'),
    ("a.md", '    JWT_SECRET: str = "CHANGE_ME"'),
    ("a.md", '    jwt_secret: SecretStr = Field(validation_alias="JWT_SECRET")'),
    ("a.md", "  JWT_SECRET: str;"),
    ("a.md", "    idempotency_key: str"),
    ("a.md", "primary_key=True,"),
    ("a.md", "JWT_SECRET: required in prod"),
    ("a.md", "api_token=abc"),
    ("a.mts", "  api_key: string;"),
    ("a.cts", "  api_key: string;"),
    ("a.py", 'cache_key = f"cards:{oracle_id}"'),
    ("a.py", "cache_key: str = f'cards:{oracle_id}'"),
]


@pytest.mark.parametrize(("path", "line"), LITERAL_NEGATIVES)
def test_annotations_lowercase_names_and_fstring_references_are_clean(path: str, line: str) -> None:
    assert rules(path, line + "\n") == []


def test_fstring_without_braces_is_still_a_literal() -> None:
    assert rules("a.py", f'API_KEY = f"{SECRET_VALUE}"\n') == [(1, "secret-assignment")]


@pytest.mark.parametrize("path", ["a.mts", "a.cts"])
def test_mts_and_cts_quoted_literal_is_flagged(path: str) -> None:
    assert rules(path, f"const API_KEY = '{SECRET_VALUE}'\n") == [(1, "secret-assignment")]


def test_markdown_line_start_env_assignment_is_still_flagged() -> None:
    assert rules("a.md", f"X_TOKEN={SECRET_VALUE}\n") == [(1, "secret-assignment")]


@pytest.mark.parametrize(
    "line",
    [
        "connect with user:pass@ style URLs",
        "postgresql://user:${DB_PASSWORD}@localhost/db",
        "postgresql://user:@localhost/db",
        "postgresql://user:CHANGE_ME@localhost/db",
        "redis://:CHANGE_ME@localhost",
        "redis://:${REDIS_PASSWORD}@localhost",
    ],
)
def test_url_placeholders_and_prose_are_clean(line: str) -> None:
    assert rules("a.md", line + "\n") == []


@pytest.mark.parametrize(
    ("path", "template"),
    [
        (".env", "{n}={v}  # secrets-check: allow"),
        ("a.yaml", "{n}: {v} # secrets-check: allow"),
        ("a.py", '{n} = "{v}"  # secrets-check: allow'),
        ("a.ts", 'const {n} = "{v}"; // secrets-check: allow'),
        ("a.txt", "postgres://u:{v}@host/db  # secrets-check: allow"),
    ],
)
def test_allow_marker_skips_the_line(path: str, template: str) -> None:
    line = template.format(n="X_PASSWORD", v=SECRET_VALUE)
    assert rules(path, line + "\n") == []


def test_allow_marker_skips_private_key_line() -> None:
    assert rules("k.txt", KEY_HEADER + "  # secrets-check: allow\n") == []


def test_allow_marker_only_skips_its_own_line() -> None:
    text = f"A_KEY={SECRET_VALUE}  # secrets-check: allow\nB_KEY={SECRET_VALUE}\n"
    assert rules("a.env", text) == [(2, "secret-assignment")]


def test_finding_render_has_no_value() -> None:
    findings = secrets_check.scan_text("a.env", f"A_KEY={SECRET_VALUE}\n")
    assert [f.render() for f in findings] == ["a.env:1: secret-assignment"]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (".env", True),
        (".env.local", True),
        (".env.production", True),
        ("apps/api/.env", True),
        ("apps\\web\\.env.test", True),
        (".env.example", False),
        ("apps/api/.env.example", False),
        ("docs/env.md", False),
        ("environment.py", False),
    ],
)
def test_is_forbidden_env_file(path: str, expected: bool) -> None:
    assert secrets_check.is_forbidden_env_file(path) is expected


# ------------------------------------------------------------ end to end: shared repo

# (path, content, expected finding rule, expected line). Rule None means no finding expected.
E2E_CASES: list[tuple[str, str | bytes, str | None, int]] = [
    # AC2: tracked env files
    (".env.local", "APP_ENV=dev\n", "tracked-env-file", 0),
    (".env.production", "APP_ENV=dev\n", "tracked-env-file", 0),
    ("apps/api/.env", "APP_ENV=dev\n", "tracked-env-file", 0),
    (".env.example", "# c\nAPP_ENV=dev\n", None, 0),
    # AC3 patterns
    ("cfg/a.yaml", f"# c\n\nX_TOKEN: {SECRET_VALUE}\n", "secret-assignment", 3),
    ("cfg/b.py", f'# c\n\nX_SECRET = "{SECRET_VALUE}"\n', "secret-assignment", 3),
    ("cfg/c.ts", f'// c\n\nconst X_KEY = "{SECRET_VALUE}";\n', "secret-assignment", 3),
    ("cfg/d.json", f'{{\n  "X_PASSWORD": "{SECRET_VALUE}"\n}}\n', "secret-assignment", 2),
    ("cfg/e.sh", f"#!/bin/sh\nexport X_TOKEN={SECRET_VALUE}\n", "secret-assignment", 2),
    ("keys/id.pem", f"x\n{KEY_HEADER}\n", "private-key", 2),
    ("docs/url.md", f"x\n\nredis://:{SECRET_VALUE}@host\n", "inline-password", 3),
    ("docs/url2.md", f"x\npostgres://u:{SECRET_VALUE}@host/db\n", "inline-password", 2),
    # non-ASCII and space-containing paths
    ("caf\u00e9/\u00fcber secret.yaml", f"X_TOKEN: {SECRET_VALUE}\n", "secret-assignment", 1),
    ("with space/a b.py", f'X_KEY = "{SECRET_VALUE}"\n', "secret-assignment", 1),
    ("\u65e5\u672c/.env.test", "A=1\n", "tracked-env-file", 0),
    # UTF-16 with BOM is scanned
    ("enc/utf16.env", f"x\nX_TOKEN={SECRET_VALUE}\n".encode("utf-16"), "secret-assignment", 2),
    # negatives
    ("ok/placeholder.env", "X_SECRET=CHANGE_ME\nX_KEY=\nX_TOKEN=${OTHER}\n", None, 0),
    ("ok/code.py", 'x = settings.JWT_SECRET\ny = os.environ["X_TOKEN"]\n', None, 0),
    ("ok/allowed.env", f"X_TOKEN={SECRET_VALUE}  # secrets-check: allow\n", None, 0),
    ("ok/binary.env", f"X_TOKEN={SECRET_VALUE}\n".encode() + b"\0\1", None, 0),
    ("ok/caf\u00e9 clean.md", "just prose\n", None, 0),
]


@pytest.fixture(scope="module")
def shared_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not HAS_GIT:
        pytest.skip("git is not installed")
    repo = init_repo(tmp_path_factory.mktemp("shared"))
    (repo / ".gitignore").write_text("/.env\n", encoding="utf-8")
    write(repo, ".env", f"X_TOKEN={SECRET_VALUE}\n", add=False)  # untracked and ignored
    for path, content, _, _ in E2E_CASES:
        write(repo, path, content, add=False)
    git(repo, "add", "-A")
    return repo


@pytest.fixture(scope="module", params=["default", "staged"])
def shared_run(request: pytest.FixtureRequest, shared_repo: Path) -> Result:
    return run_main(shared_repo, staged=request.param == "staged")


def reported(result: Result, path: str) -> list[str]:
    return [ln for ln in result.lines if ln.startswith(f"{path}:")]


def test_shared_run_exits_1_and_never_prints_the_value(shared_run: Result) -> None:
    assert shared_run.code == 1
    assert SECRET_VALUE not in shared_run.out + shared_run.err


def test_untracked_ignored_env_file_is_not_reported(shared_run: Result) -> None:
    assert reported(shared_run, ".env") == []


@pytest.mark.parametrize(
    ("path", "rule", "line"),
    [(p, r, n) for p, _, r, n in E2E_CASES if r is not None],
    ids=[p for p, _, r, _ in E2E_CASES if r is not None],
)
def test_shared_run_names_the_file_and_line(
    shared_run: Result, path: str, rule: str, line: int
) -> None:
    expected = f"{path}: {rule}" if line == 0 else f"{path}:{line}: {rule}"
    assert expected in shared_run.lines


@pytest.mark.parametrize(
    "path", [p for p, _, r, _ in E2E_CASES if r is None], ids=lambda p: p.replace("/", "-")
)
def test_shared_run_does_not_report_clean_files(shared_run: Result, path: str) -> None:
    assert reported(shared_run, path) == []


@requires_git
@pytest.mark.parametrize("mode", [[], ["--staged"]])
def test_cli_with_non_ascii_paths_prints_utf8_under_a_cp1252_pipe(
    shared_repo: Path, mode: list[str]
) -> None:
    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *mode],
        cwd=shared_repo,
        env=env,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert "caf\u00e9/\u00fcber secret.yaml:1: secret-assignment".encode() in result.stdout
    assert SECRET_VALUE.encode() not in result.stdout + result.stderr
    assert b"Traceback" not in result.stderr


# ------------------------------------------------------------ end to end: distinct states


@requires_git
def test_untracked_env_file_is_not_checked(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    write(repo, ".env", "APP_ENV=dev\n", add=False)
    assert run_main(repo) == Result(0, "", "")


@requires_git
def test_staged_mode_ignores_unstaged_env_file(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    write(repo, "README.md", "hi\n")
    write(repo, ".env.local", "APP_ENV=dev\n", add=False)
    assert run_main(repo, staged=True) == Result(0, "", "")


@requires_git
def test_staged_mode_fails_on_staged_env_local(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    write(repo, ".env.local", "APP_ENV=dev\n")
    result = run_main(repo, staged=True)
    assert result.code == 1
    assert ".env.local: tracked-env-file" in result.lines


@requires_git
def test_staged_mode_scans_staged_content_not_working_tree(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    write(repo, "app/settings.py", f'JWT_SECRET = "{SECRET_VALUE}"\n')
    write(repo, "app/settings.py", "x = 1\n", add=False)
    result = run_main(repo, staged=True)
    assert result.code == 1
    assert "app/settings.py:1: secret-assignment" in result.lines
    assert SECRET_VALUE not in result.out + result.err


@requires_git
def test_staged_mode_passes_when_staged_content_is_clean(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    write(repo, "app/settings.py", "x = 1\n")
    write(repo, "app/settings.py", f'JWT_SECRET = "{SECRET_VALUE}"\n', add=False)
    assert run_main(repo, staged=True) == Result(0, "", "")


@requires_git
def test_default_mode_scans_the_working_tree_of_tracked_files(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    write(repo, "a.yaml", "x: 1\n")
    write(repo, "a.yaml", f"A_TOKEN: {SECRET_VALUE}\n", add=False)
    result = run_main(repo)
    assert result.code == 1
    assert "a.yaml:1: secret-assignment" in result.lines


@requires_git
def test_staged_rename_scans_the_new_path(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    write(repo, "a.yaml", "x: 1\n")
    write(repo, "notes.txt", "hello\n")
    commit(repo)
    git(repo, "mv", "a.yaml", "b.yaml")
    write(repo, "b.yaml", f"A_TOKEN: {SECRET_VALUE}\n")
    git(repo, "mv", "notes.txt", ".env.prod")
    result = run_main(repo, staged=True)
    assert result.code == 1
    assert set(result.lines) == {"b.yaml:1: secret-assignment", ".env.prod: tracked-env-file"}


@requires_git
def test_staged_delete_of_a_bad_file_is_clean(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    write(repo, ".env.old", f"A_TOKEN={SECRET_VALUE}\n")
    write(repo, "keep.md", "x\n")
    commit(repo)
    git(repo, "rm", "-q", ".env.old")
    assert run_main(repo, staged=True) == Result(0, "", "")


@requires_git
def test_staged_type_change_to_a_regular_file_is_scanned(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    link = git(repo, "hash-object", "-w", "--stdin", stdin=b"target.txt").decode().strip()
    git(repo, "update-index", "--add", "--cacheinfo", f"120000,{link},link.yaml")
    commit(repo)
    body = f"A_TOKEN: {SECRET_VALUE}\n".encode()
    blob = git(repo, "hash-object", "-w", "--stdin", stdin=body).decode().strip()
    git(repo, "update-index", "--cacheinfo", f"100644,{blob},link.yaml")
    staged_type = git(repo, "diff", "--cached", "--raw", "--no-renames").decode()
    assert " T\t" in staged_type
    result = run_main(repo, staged=True)
    assert result.code == 1
    assert "link.yaml:1: secret-assignment" in result.lines


@requires_git
def test_staged_type_change_to_a_symlink_does_not_crash(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    write(repo, "link.yaml", "x: 1\n")
    commit(repo)
    target = git(repo, "hash-object", "-w", "--stdin", stdin=b"target.txt").decode().strip()
    git(repo, "update-index", "--cacheinfo", f"120000,{target},link.yaml")
    assert run_main(repo, staged=True).code == 0


@requires_git
def test_default_mode_does_not_scan_a_symlink_entry_even_if_the_file_is_regular(
    tmp_path: Path,
) -> None:
    """Index mode 120000 with a regular file on disk (core.symlinks=false): not scanned."""
    repo = init_repo(tmp_path)
    write(repo, "link.yaml", f"A_TOKEN: {SECRET_VALUE}\n", add=False)
    blob = git(repo, "hash-object", "-w", "--stdin", stdin=b"target.yaml").decode().strip()
    git(repo, "update-index", "--add", "--cacheinfo", f"120000,{blob},link.yaml")
    assert run_main(repo) == Result(0, "", "")


@requires_git
def test_default_mode_does_not_follow_a_real_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "outside.yaml"
    outside.write_text(f"A_TOKEN: {SECRET_VALUE}\n", encoding="utf-8")
    repo = init_repo(tmp_path / "repo")
    try:
        (repo / "link.yaml").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("cannot create symlinks here (Windows without developer mode)")
    git(repo, "-c", "core.symlinks=true", "add", "link.yaml")
    mode = git(repo, "ls-files", "-s", "link.yaml").split()[0]
    if mode != b"120000":
        pytest.skip("git did not record a symlink (core.symlinks=false)")
    assert run_main(repo) == Result(0, "", "")


@requires_git
def test_binary_file_is_skipped(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    write(repo, "blob.env", f"A_KEY={SECRET_VALUE}\n".encode() + b"\0\1\2")
    assert run_main(repo) == Result(0, "", "")


@requires_git
def test_clean_repo_exits_zero(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    write(repo, "README.md", "hello\n")
    assert run_main(repo) == Result(0, "", "")


@requires_git
@pytest.mark.parametrize("mode", [[], ["--staged"]])
def test_exit_code_2_when_cwd_is_not_a_git_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: list[str],
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    monkeypatch.chdir(outside)
    assert secrets_check.main(mode) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "secrets-check: cannot run git" in captured.err


# ------------------------------------------------------------ AC1: .env.example


def parse_env_example() -> list[tuple[int, str, str, str | None]]:
    """Return (line number, name, value, comment directly above or None)."""
    entries: list[tuple[int, str, str, str | None]] = []
    previous: str | None = None
    for number, raw in enumerate(ENV_EXAMPLE.read_text(encoding="utf-8").splitlines(), start=1):
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", raw)
        if match:
            entries.append((number, match.group(1), match.group(2).strip(), previous))
        previous = raw if raw.startswith("#") else None
    return entries


def env_example_names() -> set[str]:
    return {name for _, name, _, _ in parse_env_example()}


def test_env_example_lists_variables() -> None:
    assert parse_env_example()


def test_env_example_every_variable_has_a_comment_line_directly_above() -> None:
    missing = [
        name
        for _, name, _, comment in parse_env_example()
        if comment is None or not comment.lstrip("# ").strip()
    ]
    assert missing == []


def test_env_example_values_are_change_me_or_a_nonempty_dev_default() -> None:
    bad = [name for _, name, value, _ in parse_env_example() if value == ""]
    assert bad == []


def test_env_example_secret_named_variables_are_change_me_or_empty() -> None:
    bad = [
        name
        for _, name, value, _ in parse_env_example()
        if re.search(r"_(SECRET|KEY|TOKEN|PASSWORD)$", name, re.IGNORECASE)
        and value not in ("", "CHANGE_ME")
    ]
    assert bad == []


@pytest.mark.parametrize("group", ["Docker Compose", "API and worker", "Web", "Mobile"])
def test_env_example_has_group(group: str) -> None:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert re.search(rf"^#.*{re.escape(group)}", text, re.MULTILINE), group


def settings_variable_names() -> set[str]:
    """Env names of every field of the API ``Settings`` (validation alias or upper-cased name)."""
    api_root = REPO_ROOT / "apps" / "api"
    config = api_root / "app" / "core" / "config.py"
    if not config.is_file():
        pytest.skip("apps/api/app/core/config.py not present yet (S-1.4.1 adds it)")
    sys.path.insert(0, str(api_root))
    try:
        from app.core.config import Settings  # type: ignore[import-not-found,unused-ignore]  # noqa: PLC0415
    finally:
        sys.path.remove(str(api_root))
    names: set[str] = set()
    for field_name, field in Settings.model_fields.items():
        alias = field.validation_alias
        choices = getattr(alias, "choices", None)
        if isinstance(alias, str):
            names.add(alias)
        elif choices:
            names.update(c for c in choices if isinstance(c, str))
        else:
            names.add(field_name.upper())
    return names


def test_env_example_declares_every_api_settings_variable() -> None:
    assert settings_variable_names() - env_example_names() == set()


TS_PATTERNS = ("*.ts", "*.tsx", "*.mts", "*.cts")
VITE_DOT = r"import\.meta\.env\.(VITE_\w+)"
VITE_BRACKET = r"import\.meta\.env\[\s*[\"'](VITE_\w+)[\"']\s*\]"


def source_files(
    root: Path, patterns: tuple[str, ...], skip_dirs: tuple[str, ...] = ()
) -> list[Path]:
    if not root.is_dir():
        return []
    return [
        file
        for pattern in patterns
        for file in root.rglob(pattern)
        if not any(part in skip_dirs for part in file.relative_to(root).parts)
    ]


def grep_names(files: list[Path], *patterns: str) -> set[str]:
    found: set[str] = set()
    for file in files:
        if not file.is_file():
            continue
        text = file.read_text(encoding="utf-8", errors="replace")
        for pattern in patterns:
            found.update(re.findall(pattern, text))
    return found


def test_grep_patterns_find_dot_and_bracket_forms(tmp_path: Path) -> None:
    file = tmp_path / "a.ts"
    file.write_text(
        "const a = import.meta.env.VITE_ONE;\n"
        "const b = import.meta.env['VITE_TWO'];\n"
        'const c = import.meta.env[ "VITE_THREE" ];\n',
        encoding="utf-8",
    )
    assert grep_names([file], VITE_DOT, VITE_BRACKET) == {"VITE_ONE", "VITE_TWO", "VITE_THREE"}


def test_env_example_declares_every_vite_variable_the_web_app_reads() -> None:
    web = REPO_ROOT / "apps" / "web"
    used = grep_names(source_files(web / "src", TS_PATTERNS), VITE_DOT, VITE_BRACKET)
    used |= grep_names(list(web.glob("vite.config.*")), r"\b(VITE_\w+)")
    assert used - env_example_names() == set()


def test_env_example_declares_every_expo_variable_the_mobile_app_reads() -> None:
    mobile = REPO_ROOT / "apps" / "mobile"
    files = source_files(mobile, TS_PATTERNS, skip_dirs=("node_modules", ".expo"))
    used = grep_names(files, r"process\.env\.(EXPO_PUBLIC_\w+)")
    used |= grep_names(list(mobile.glob("app.config.*")), r"process\.env\.(\w+)")
    assert used - env_example_names() == set()


# ------------------------------------------------------------ real repository


@requires_git
def test_real_repository_has_no_findings() -> None:
    findings = secrets_check.check_repo(REPO_ROOT)
    assert [f.render() for f in findings] == []

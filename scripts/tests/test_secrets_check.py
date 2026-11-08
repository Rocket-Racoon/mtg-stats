"""Tests for the secrets check and ``.env.example`` (S-1.3.3), from the acceptance criteria."""

# ruff: noqa: S101, S603, S607

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import secrets_check

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = REPO_ROOT / ".env.example"

requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

# Built by concatenation so this file never trips the check itself.
KEY_HEADER = "-----BEGIN " + "RSA PRIVATE" + " KEY-----"
SECRET_VALUE = "zq9" + "Xv7wLmN3pQ"
SENSITIVE_SUFFIXES = ["SECRET", "KEY", "TOKEN", "PASSWORD"]


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    git(tmp_path, "init", "-q")
    return tmp_path


def write(repo: Path, rel: str, text: str, *, add: bool = True) -> None:
    file = repo / rel
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text, encoding="utf-8")
    if add:
        git(repo, "add", "--", rel)


def run(
    repo: Path, capsys: pytest.CaptureFixture[str], *, staged: bool = False
) -> tuple[int, str, str]:
    capsys.readouterr()
    code = secrets_check.main(["--staged"] if staged else [], repo=repo)
    out = capsys.readouterr()
    return code, out.out, out.err


# ---------------------------------------------------------------- AC2: env files


@pytest.mark.parametrize("path", [".env", ".env.local", ".env.production", "apps/api/.env"])
def test_tracked_env_file_fails_and_is_named(
    repo: Path, capsys: pytest.CaptureFixture[str], path: str
) -> None:
    write(repo, path, "APP_ENV=dev\n")
    code, out, _ = run(repo, capsys)
    assert code == 1
    assert f"{path}: tracked-env-file" in out.splitlines()


def test_tracked_env_example_is_allowed(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(repo, ".env.example", "# note\nAPP_ENV=dev\n")
    code, out, _ = run(repo, capsys)
    assert (code, out) == (0, "")


def test_untracked_env_file_is_not_checked(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(repo, ".env", "APP_ENV=dev\n", add=False)
    code, out, _ = run(repo, capsys)
    assert (code, out) == (0, "")


def test_staged_mode_fails_on_staged_env_local(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(repo, ".env.local", "APP_ENV=dev\n")
    code, out, _ = run(repo, capsys, staged=True)
    assert code == 1
    assert ".env.local: tracked-env-file" in out


def test_staged_mode_ignores_unstaged_env_file(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(repo, ".env.local", "APP_ENV=dev\n", add=False)
    code, out, _ = run(repo, capsys, staged=True)
    assert (code, out) == (0, "")


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


def test_exit_code_2_when_not_a_git_repository(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    code, _, _ = run(tmp_path, capsys)
    assert code == 2


# ---------------------------------------------------------------- AC3: positives

# (file path, line template). ``{n}`` is the variable name, ``{v}`` the secret literal.
ASSIGNMENT_FORMS = [
    (".env", "{n}={v}"),
    ("config/app.yaml", "{n}: {v}"),
    ("config/app.yml", '{n}: "{v}"'),
    ("app/settings.py", '{n} = "{v}"'),
    ("app/settings.py", '{n}: str = "{v}"'),
    ("app/settings.py", "CONFIG['{n}'] = '{v}'"),
    ("web/src/config.ts", 'const {n} = "{v}";'),
    ("web/src/config.ts", '  "{n}": "{v}",'),
]


@pytest.mark.parametrize("suffix", SENSITIVE_SUFFIXES)
@pytest.mark.parametrize(("path", "template"), ASSIGNMENT_FORMS)
def test_secret_assignment_fails_naming_path_and_line(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    suffix: str,
    path: str,
    template: str,
) -> None:
    line = template.format(n=f"JWT_{suffix}", v=SECRET_VALUE)
    write(repo, path, f"# header\n\n{line}\n")
    code, out, err = run(repo, capsys)
    assert code == 1
    assert any(entry.startswith(f"{path}:3:") for entry in out.splitlines())
    assert SECRET_VALUE not in out + err


def test_private_key_block_fails_naming_path_and_line(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(repo, "keys/id.txt", f"hello\n{KEY_HEADER}\nabcd\n")
    code, out, _ = run(repo, capsys)
    assert code == 1
    assert "keys/id.txt:2:" in out


@pytest.mark.parametrize("kind", ["EC ", "OPENSSH ", ""])
def test_private_key_variants_fail(
    repo: Path, capsys: pytest.CaptureFixture[str], kind: str
) -> None:
    write(repo, "k.pem", "-----BEGIN " + kind + "PRIVATE" + " KEY-----\n")
    code, out, _ = run(repo, capsys)
    assert code == 1
    assert "k.pem:1:" in out


@pytest.mark.parametrize("scheme", ["postgresql+psycopg", "redis", "https"])
def test_connection_string_with_inline_password_fails(
    repo: Path, capsys: pytest.CaptureFixture[str], scheme: str
) -> None:
    url = f"{scheme}://app:{SECRET_VALUE}@db.internal:5432/mtg"
    write(repo, "docs/setup.md", f"intro\n\nuse {url} here\n")
    code, out, err = run(repo, capsys)
    assert code == 1
    assert "docs/setup.md:3:" in out
    assert SECRET_VALUE not in out + err


def test_staged_mode_scans_staged_content_not_working_tree(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(repo, "app/settings.py", f'JWT_SECRET = "{SECRET_VALUE}"\n')
    write(repo, "app/settings.py", "x = 1\n", add=False)  # working tree is clean now
    code, out, err = run(repo, capsys, staged=True)
    assert code == 1
    assert "app/settings.py:1:" in out
    assert SECRET_VALUE not in out + err


def test_staged_mode_passes_when_staged_content_is_clean(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(repo, "app/settings.py", "x = 1\n")
    write(repo, "app/settings.py", f'JWT_SECRET = "{SECRET_VALUE}"\n', add=False)
    code, out, _ = run(repo, capsys, staged=True)
    assert (code, out) == (0, "")


def test_default_mode_scans_tracked_files_without_printing_value(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(repo, "a.yaml", f"A_TOKEN: {SECRET_VALUE}\n")
    code, out, err = run(repo, capsys)
    assert code == 1
    assert "a.yaml:1:" in out
    assert SECRET_VALUE not in out + err


# ---------------------------------------------------------------- AC3: negatives

NEGATIVE_LINES = [
    ("a.env", "X_SECRET=CHANGE_ME"),
    ("a.env", "X_KEY" + "="),  # concatenated: see report on quoted ``NAME=`` text
    ("a.env", "X_TOKEN=${SOME_TOKEN}"),
    ("a.yaml", "x_password: CHANGE_ME"),
    ("a.yaml", 'X_PASSWORD: ""'),
    ("a.yaml", "X_SECRET: ${{ secrets.X_SECRET }}"),
    ("a.py", 'X_SECRET = "CHANGE_ME"'),
    ("a.py", 'X_KEY = ""'),
    ("a.py", "x = settings.JWT_SECRET"),
    ("a.py", "JWT_SECRET = settings.JWT_SECRET"),
    ("a.py", 'token = os.environ["X_TOKEN"]'),
    ("a.py", 'if os.environ["X_TOKEN"] == "abc":'),
    ("a.py", 'if X_KEY == "abc":'),
    ("a.ts", "const k = process.env.API_KEY;"),
    ("a.ts", 'if (X_TOKEN === "abc") {}'),
    ("a.ts", 'const t = import.meta.env["VITE_X_TOKEN"];'),
    ("a.md", "Set DATABASE_PASSWORD to your own value."),
    ("a.md", "connect with user:pass@ style URLs"),
    ("a.md", "postgresql://user:${DB_PASSWORD}@localhost/db"),
    ("a.md", "postgresql://user:@localhost/db"),
    ("a.md", "postgresql://user:CHANGE_ME@localhost/db"),
]


@pytest.mark.parametrize(("path", "line"), NEGATIVE_LINES)
def test_placeholders_references_and_comparisons_are_clean(path: str, line: str) -> None:
    assert secrets_check.scan_text(path, line + "\n") == []


@pytest.mark.parametrize(
    ("path", "template"),
    [
        (".env", "X_SECRET={v}  # secrets-check: allow"),
        ("a.yaml", "X_TOKEN: {v} # secrets-check: allow"),
        ("a.py", 'X_PASSWORD = "{v}"  # secrets-check: allow'),
        ("a.ts", 'const X_KEY = "{v}"; // secrets-check: allow'),
        ("a.txt", "postgres://u:{v}@host/db  # secrets-check: allow"),
    ],
)
def test_allow_marker_skips_the_line(path: str, template: str) -> None:
    assert secrets_check.scan_text(path, template.format(v=SECRET_VALUE) + "\n") == []


def test_allow_marker_skips_private_key_line() -> None:
    assert secrets_check.scan_text("k.txt", KEY_HEADER + "  # secrets-check: allow\n") == []


def test_allow_marker_only_skips_its_own_line() -> None:
    text = f"A_KEY={SECRET_VALUE}  # secrets-check: allow\nB_KEY={SECRET_VALUE}\n"
    findings = secrets_check.scan_text("a.env", text)
    assert [f.line for f in findings] == [2]


def test_binary_file_is_skipped(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    file = repo / "blob.env"
    file.write_bytes(f"A_KEY={SECRET_VALUE}\n".encode() + b"\0\1\2")
    git(repo, "add", "--", "blob.env")
    code, out, _ = run(repo, capsys)
    assert (code, out) == (0, "")


def test_clean_repo_exits_zero(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(repo, "README.md", "hello\n")
    code, out, _ = run(repo, capsys)
    assert (code, out) == (0, "")


def test_finding_render_has_no_value() -> None:
    findings = secrets_check.scan_text("a.env", f"A_KEY={SECRET_VALUE}\n")
    assert [f.render() for f in findings] == ["a.env:1: secret-assignment"]


# ---------------------------------------------------------------- AC1: .env.example


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


def test_env_example_contains_every_url_and_env_variable_declared_by_api_config() -> None:
    """Light check; S-1.4.1, S-1.6.1 and S-1.7.1 add their variables on separate branches."""
    config = REPO_ROOT / "apps" / "api" / "app" / "core" / "config.py"
    if not config.is_file():
        pytest.skip("apps/api/app/core/config.py not present yet (S-1.4.1 adds it)")
    declared = {
        m.group(1).upper()
        for m in re.finditer(
            r"^\s+([A-Za-z_]*(?:url|URL|app_env|APP_ENV))\s*:",
            config.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    }
    present = {name for _, name, _, _ in parse_env_example()}
    assert declared - present == set()


# ---------------------------------------------------------------- real repository


@requires_git
def test_real_repository_has_no_findings() -> None:
    findings = secrets_check.check_repo(REPO_ROOT)
    assert [f.render() for f in findings] == []

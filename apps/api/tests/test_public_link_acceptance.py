"""S-1.6.7 AC1: ``public_link()`` shape, round-trip, settings rules and the static guards."""

import ast
import io
import json
import tokenize
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.core.links import public_link
from app.main import create_app
from tests.repo_paths import repo_root

DB_URL = "postgresql+psycopg://user:CHANGE_ME@db.example.com:5432/mtg"
REDIS_URL = "redis://127.0.0.1:6379/0"
BASE = "https://app.example.com"

ROOT = repo_root(__file__)
APP_DIR = Path(__file__).resolve().parents[1] / "app"
OPENAPI = ROOT / "packages" / "api-client" / "openapi.json"

AWKWARD = [
    "plain",
    "100%",
    "%2F",
    "a+b",
    "a&b=c",
    "..",
    "../..",
    "a/b/c",
    "with space",
    "a?b#c",
    "café",
    "日本語",
    "\U0001f9d9 deck \U0001f525",
    "q\"'<>\\",
]


def _settings(url: str | None, env: str = "test") -> Settings:
    values = {"APP_ENV": env, "DATABASE_URL": DB_URL, "REDIS_URL": REDIS_URL}
    if url is not None:
        values["PUBLIC_WEB_URL"] = url
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PUBLIC_WEB_URL", raising=False)


@pytest.mark.parametrize("kind", ["invite", "reset", "join"])
@pytest.mark.parametrize("value", AWKWARD)
def test_link_has_exact_shape_and_value_round_trips(kind: str, value: str) -> None:
    link = public_link(kind, value, settings=_settings(BASE))  # type: ignore[arg-type]
    prefix = f"{BASE}/{kind}/"
    assert link.startswith(prefix)
    last = link[len(prefix) :]
    assert "/" not in last
    assert unquote(last) == value
    parts = urlsplit(link)
    assert (parts.scheme, parts.netloc) == ("https", "app.example.com")
    assert parts.query == ""
    assert parts.fragment == ""
    assert parts.path == f"/{kind}/{last}"


@pytest.mark.parametrize(
    ("value", "encoded"),
    [("100%", "100%25"), ("a+b", "a%2Bb"), ("a&b=c", "a%26b%3Dc"), ("a/b", "a%2Fb")],
)
def test_percent_plus_ampersand_equals_and_slash_are_encoded(value: str, encoded: str) -> None:
    assert public_link("invite", value, settings=_settings(BASE)) == f"{BASE}/invite/{encoded}"


def test_dot_dot_value_stays_a_single_path_segment() -> None:
    link = public_link("join", "..", settings=_settings(BASE))
    assert urlsplit(link).path.count("/") == 2
    assert unquote(link.rsplit("/", 1)[1]) == ".."


def test_link_uses_the_configured_value_not_a_constant() -> None:
    a = public_link("join", "x", settings=_settings("https://a.example.org"))
    b = public_link("join", "x", settings=_settings("https://b.example.org"))
    assert (a, b) == ("https://a.example.org/join/x", "https://b.example.org/join/x")


def test_link_without_explicit_settings_reads_public_web_url_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PUBLIC_WEB_URL", "https://env.example.net/")
    get_settings.cache_clear()
    try:
        assert public_link("reset", "t") == "https://env.example.net/reset/t"
    finally:
        get_settings.cache_clear()


def test_missing_public_web_url_fails_building_settings() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _settings(None)
    errors = excinfo.value.errors()
    assert any(e["loc"] == ("PUBLIC_WEB_URL",) and e["type"] == "missing" for e in errors)


def test_missing_public_web_url_fails_app_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    monkeypatch.setenv("REDIS_URL", REDIS_URL)
    monkeypatch.setattr("app.core.config.find_env_file", lambda: None)
    get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError):
            create_app()
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("env", ["dev", "test"])
def test_http_is_allowed_outside_prod(env: str) -> None:
    assert _settings("http://localhost:5173", env=env).public_web_url == "http://localhost:5173"


def test_http_is_rejected_in_prod() -> None:
    with pytest.raises(ValidationError, match="https"):
        _settings("http://app.example.com", env="prod")


def test_https_is_accepted_in_prod() -> None:
    assert _settings(BASE, env="prod").public_web_url == BASE


# ---- static guards ----------------------------------------------------------------------------

_SOURCE_FILES = sorted(
    p for p in APP_DIR.rglob("*.py") if "tests" not in p.relative_to(APP_DIR).parts
)
_DEFINERS = {Path("core/config.py"), Path("core/links.py")}
# Supplies a placeholder PUBLIC_WEB_URL to build Settings for the spec export; builds no link.
_SETTINGS_SUPPLIERS = {Path("scripts/export_openapi.py")}
_LINKS = Path("core/links.py")


def _is_public_web_url_ref(node: ast.AST) -> bool:
    if isinstance(node, ast.Attribute):
        return node.attr == "public_web_url"
    if isinstance(node, ast.Name):
        return node.id == "public_web_url"
    if isinstance(node, ast.keyword):
        return node.arg == "public_web_url"
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return "PUBLIC_WEB_URL" in node.value or "public_web_url" in node.value
    return False


def _docstring_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                ids.add(id(first.value))
    return ids


def _scheme_literals(source: str, *, allow_docstrings: bool) -> list[str]:
    tree = ast.parse(source)
    docs = _docstring_ids(tree) if allow_docstrings else set()
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "mtgstats://" in node.value
        and id(node) not in docs
    ]


def test_source_scan_finds_the_api_sources() -> None:
    names = {p.relative_to(APP_DIR) for p in _SOURCE_FILES}
    assert names >= _DEFINERS
    assert len(names) > 10


def test_only_config_and_links_touch_public_web_url() -> None:
    offenders = []
    for path in _SOURCE_FILES:
        if path.relative_to(APP_DIR) in _DEFINERS | _SETTINGS_SUPPLIERS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(_is_public_web_url_ref(node) for node in ast.walk(tree)):
            offenders.append(str(path.relative_to(APP_DIR)))
    assert offenders == []


def test_no_mtgstats_string_literal_in_api_sources() -> None:
    """A literal anywhere is a violation; only core/links.py's docstring may name the scheme."""
    offenders = [
        str(path.relative_to(APP_DIR))
        for path in _SOURCE_FILES
        if _scheme_literals(
            path.read_text(encoding="utf-8"), allow_docstrings=path.relative_to(APP_DIR) == _LINKS
        )
    ]
    assert offenders == []


def test_mtgstats_scheme_outside_links_module_appears_nowhere_but_comments() -> None:
    offenders = []
    for path in _SOURCE_FILES:
        if path.relative_to(APP_DIR) == _LINKS:
            continue
        src = path.read_text(encoding="utf-8")
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type != tokenize.COMMENT and "mtgstats://" in tok.string:
                offenders.append(str(path.relative_to(APP_DIR)))
    assert offenders == []


def test_scheme_guard_catches_literals_and_fstrings_but_not_comments() -> None:
    assert _scheme_literals('X = "mtgstats://join/x"', allow_docstrings=True) == [
        "mtgstats://join/x"
    ]
    assert _scheme_literals('X = f"mtgstats://join/{y}"', allow_docstrings=True) != []
    assert _scheme_literals('"""mtgstats://x"""\nX = 1', allow_docstrings=False) != []
    assert _scheme_literals('"""mtgstats://x"""\nX = 1', allow_docstrings=True) == []
    assert _scheme_literals("# mtgstats://x\nX = 1", allow_docstrings=False) == []


def test_public_web_url_guard_catches_a_read_but_not_a_comment() -> None:
    assert any(_is_public_web_url_ref(n) for n in ast.walk(ast.parse("s.public_web_url")))
    assert any(
        _is_public_web_url_ref(n) for n in ast.walk(ast.parse("os.environ['PUBLIC_WEB_URL']"))
    )
    assert not any(
        _is_public_web_url_ref(n) for n in ast.walk(ast.parse("# public_web_url\nx = 1"))
    )


def test_committed_openapi_spec_offers_no_mtgstats_link() -> None:
    if not OPENAPI.is_file():
        pytest.skip("repo-level spec not available (API-only container)")
    text = OPENAPI.read_text(encoding="utf-8")
    json.loads(text)
    assert "mtgstats://" not in text

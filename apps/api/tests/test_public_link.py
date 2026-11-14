"""``PUBLIC_WEB_URL`` setting and ``public_link()`` (S-1.6.7 T-1.6.7.1)."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.core.links import LINK_KINDS, public_link

DB_URL = "postgresql+psycopg://user:CHANGE_ME@db.example.com:5432/mtg"
REDIS_URL = "redis://127.0.0.1:6379/0"


def _settings(url: str | None, env: str = "test") -> Settings:
    values = {"APP_ENV": env, "DATABASE_URL": DB_URL, "REDIS_URL": REDIS_URL}
    if url is not None:
        values["PUBLIC_WEB_URL"] = url
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def _link(kind: str, value: str, base: str = "https://app.example.com") -> str:
    return public_link(kind, value, settings=_settings(base))  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _no_env_public_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit values only: the suite-wide PUBLIC_WEB_URL must not fill in a missing one."""
    monkeypatch.delenv("PUBLIC_WEB_URL", raising=False)


@pytest.mark.parametrize("kind", ["invite", "reset", "join"])
def test_link_joins_base_kind_and_value(kind: str) -> None:
    assert _link(kind, "abc123") == f"https://app.example.com/{kind}/abc123"


def test_link_kinds_are_exactly_invite_reset_join() -> None:
    assert {"invite", "reset", "join"} == LINK_KINDS


@pytest.mark.parametrize(
    ("value", "encoded"),
    [
        ("a/b", "a%2Fb"),
        ("a b", "a%20b"),
        ("a?b=c", "a%3Fb%3Dc"),
        ("a#b", "a%23b"),
        ("café", "caf%C3%A9"),
        ("../x", "..%2Fx"),
    ],
)
def test_value_is_fully_percent_encoded(value: str, encoded: str) -> None:
    assert _link("invite", value) == f"https://app.example.com/invite/{encoded}"


def test_trailing_slashes_on_the_base_are_stripped() -> None:
    assert _link("join", "x", "https://app.example.com//") == "https://app.example.com/join/x"


def test_path_prefix_in_the_base_is_kept() -> None:
    assert _link("reset", "x", "https://example.com/mtg/") == "https://example.com/mtg/reset/x"


def test_unknown_kind_raises() -> None:
    with pytest.raises(ValueError, match="unknown link kind"):
        _link("deeplink", "x")


def test_empty_value_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        _link("invite", "")


def test_link_uses_cached_settings_when_none_given(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUBLIC_WEB_URL", "http://localhost:5173")
    get_settings.cache_clear()
    try:
        assert public_link("invite", "t") == "http://localhost:5173/invite/t"
    finally:
        get_settings.cache_clear()


def test_link_is_never_a_custom_scheme() -> None:
    assert not _link("join", "x").startswith("mtgstats://")


def test_missing_public_web_url_fails() -> None:
    with pytest.raises(ValidationError, match="PUBLIC_WEB_URL"):
        _settings(None)


def test_prod_rejects_http() -> None:
    with pytest.raises(ValidationError, match="https"):
        _settings("http://app.example.com", env="prod")


def test_scheme_and_host_are_lowercased_but_path_prefix_case_is_kept() -> None:
    settings = _settings("HTTPS://App.Example.com/MTG/", env="prod")
    assert settings.public_web_url == "https://app.example.com/MTG"


def test_prod_accepts_https() -> None:
    assert _settings("https://app.example.com", env="prod").public_web_url.startswith("https://")


@pytest.mark.parametrize("env", ["dev", "test"])
def test_dev_and_test_accept_http(env: str) -> None:
    assert _settings("http://localhost:5173", env=env).public_web_url == "http://localhost:5173"


@pytest.mark.parametrize(
    "bad",
    [
        "app.example.com",
        "ftp://app.example.com",
        "mtgstats://join",
        "https://",
        "https://user:secret@app.example.com",  # secrets-check: allow
        "https://app.example.com?x=secret",
        "https://app.example.com/#secret",
        "https://app.example.com:notaport",
        "https://app .example.com/secret",
        "https://app.example.com\\secret",
    ],
)
def test_bad_urls_fail_without_echoing_the_value(bad: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        _settings(bad)
    # pydantic's own docs link (https://errors.pydantic.dev/...) is not the input.
    text = str(excinfo.value).replace("https://errors.pydantic.dev", "")
    assert "secret" not in text
    assert bad not in text

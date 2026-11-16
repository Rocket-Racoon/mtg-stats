"""``issue_token`` / ``decode_token`` and the token settings (S-2.1.4 AC1, AC4), no database."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt
import pytest
from pydantic import ValidationError

from app.core.auth import JWT_ALGORITHM, InvalidTokenError, decode_token, issue_token
from app.core.config import Settings, get_settings, load_settings
from app.core.ids import uuid7
from tests.auth_support import (
    ACCESS,
    OTHER_SECRET,
    REFRESH,
    claims_for,
    encode,
    issue,
    secret,
    tamper_payload,
    tamper_signature,
    unsigned_token,
)

USER = uuid7()


@pytest.fixture(autouse=True)
def _fresh_settings() -> None:
    get_settings.cache_clear()


def _decode_raw(token: str) -> dict[str, Any]:
    return dict(jwt.decode(token, secret(), algorithms=["HS256"]))


def test_jwt_algorithm_is_hs256() -> None:
    assert JWT_ALGORITHM == "HS256"


def test_access_token_header_is_hs256() -> None:
    assert jwt.get_unverified_header(issue(USER))["alg"] == "HS256"


def test_access_token_carries_the_five_claims_with_type_access() -> None:
    claims = _decode_raw(issue(USER))
    assert {"sub", "exp", "iat", "jti", "type"} <= set(claims)
    assert claims["sub"] == str(USER)
    assert claims["type"] == "access"


def test_access_token_lives_15_minutes_by_default() -> None:
    claims = _decode_raw(issue(USER))
    assert isinstance(claims["exp"], int)
    assert isinstance(claims["iat"], int)
    assert claims["exp"] - claims["iat"] == 15 * 60


def test_refresh_token_lives_30_days_by_default() -> None:
    claims = _decode_raw(issue(USER, REFRESH))
    assert claims["type"] == "refresh"
    assert claims["exp"] - claims["iat"] == 30 * 24 * 3600


def test_issued_token_reports_its_jti_and_expiry() -> None:
    now = datetime(2020, 1, 1, 12, 0, tzinfo=UTC)
    issued = issue_token(USER, ACCESS, now=now)
    claims = jwt.decode(issued.token, secret(), algorithms=["HS256"], options={"verify_exp": False})
    assert issued.jti == claims["jti"]
    assert issued.expires_at == now + timedelta(minutes=15)
    assert claims["iat"] == int(now.timestamp())


def test_jti_is_unique_per_token() -> None:
    assert len({issue_token(USER, ACCESS).jti for _ in range(20)}) == 20


def test_lifetimes_are_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACCESS_TOKEN_TTL_MINUTES", "5")
    monkeypatch.setenv("REFRESH_TOKEN_TTL_DAYS", "7")
    get_settings.cache_clear()
    try:
        access = _decode_raw(issue(USER))
        refresh = _decode_raw(issue(USER, REFRESH))
    finally:
        get_settings.cache_clear()
    assert access["exp"] - access["iat"] == 5 * 60
    assert refresh["exp"] - refresh["iat"] == 7 * 24 * 3600


def test_decode_returns_typed_claims() -> None:
    claims = decode_token(issue(USER), expected_type=ACCESS)
    assert claims.sub == USER
    assert isinstance(claims.sub, UUID)
    assert claims.type == "access"
    assert claims.exp - claims.iat == timedelta(seconds=900)


def test_decode_accepts_a_refresh_token_when_a_refresh_is_expected() -> None:
    assert decode_token(issue(USER, REFRESH), expected_type=REFRESH).sub == USER


def test_decode_rejects_an_expired_token() -> None:
    old = datetime.now(UTC) - timedelta(minutes=16)
    with pytest.raises(InvalidTokenError):
        decode_token(issue(USER, now=old), expected_type=ACCESS)


def test_decode_accepts_a_token_one_minute_before_expiry() -> None:
    recent = datetime.now(UTC) - timedelta(minutes=14)
    assert decode_token(issue(USER, now=recent), expected_type=ACCESS).sub == USER


def test_decode_has_no_leeway_after_expiry() -> None:
    claims = claims_for(USER)
    claims["iat"] -= 1000
    claims["exp"] = int(datetime.now(UTC).timestamp()) - 2
    with pytest.raises(InvalidTokenError):
        decode_token(encode(claims), expected_type=ACCESS)


def test_decode_rejects_a_tampered_signature() -> None:
    with pytest.raises(InvalidTokenError):
        decode_token(tamper_signature(issue(USER)), expected_type=ACCESS)


def test_decode_rejects_a_tampered_payload() -> None:
    forged = tamper_payload(issue(USER), sub=str(uuid7()))
    with pytest.raises(InvalidTokenError):
        decode_token(forged, expected_type=ACCESS)


def test_decode_rejects_a_token_signed_with_another_secret() -> None:
    with pytest.raises(InvalidTokenError):
        decode_token(encode(claims_for(USER), key=OTHER_SECRET), expected_type=ACCESS)


def test_decode_rejects_alg_none() -> None:
    with pytest.raises(InvalidTokenError):
        decode_token(unsigned_token(claims_for(USER)), expected_type=ACCESS)


@pytest.mark.parametrize("alg", ["HS384", "HS512"])
def test_decode_rejects_other_hmac_algorithms(alg: str) -> None:
    with pytest.raises(InvalidTokenError):
        decode_token(encode(claims_for(USER), algorithm=alg), expected_type=ACCESS)


def test_decode_rejects_a_refresh_token_when_access_is_expected() -> None:
    with pytest.raises(InvalidTokenError):
        decode_token(issue(USER, REFRESH), expected_type=ACCESS)


def test_decode_rejects_an_access_token_when_refresh_is_expected() -> None:
    with pytest.raises(InvalidTokenError):
        decode_token(issue(USER, ACCESS), expected_type=REFRESH)


@pytest.mark.parametrize("missing", ["sub", "jti", "type", "exp", "iat"])
def test_decode_rejects_a_token_missing_a_required_claim(missing: str) -> None:
    claims = claims_for(USER)
    del claims[missing]
    with pytest.raises(InvalidTokenError):
        decode_token(encode(claims), expected_type=ACCESS)


def test_decode_rejects_a_non_uuid_subject() -> None:
    claims = claims_for(USER)
    claims["sub"] = "admin"
    with pytest.raises(InvalidTokenError):
        decode_token(encode(claims), expected_type=ACCESS)


@pytest.mark.parametrize("garbage", ["", "garbage", "a.b.c", "a.b", "....", "Bearer x"])
def test_decode_rejects_garbage(garbage: str) -> None:
    with pytest.raises(InvalidTokenError):
        decode_token(garbage, expected_type=ACCESS)


def test_invalid_token_error_is_a_401_with_the_stable_code() -> None:
    error = InvalidTokenError()
    assert error.code == "invalid_token"
    assert error.status_code == 401
    assert error.message == "Invalid or expired token."


# ------------------------------------------------------------------ settings


def _load(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return load_settings()


def test_settings_require_jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JWT_SECRET")
    with pytest.raises(ValidationError) as caught:
        load_settings()
    assert "JWT_SECRET" in str(caught.value)


def test_settings_reject_a_short_jwt_secret_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    short = "s3cret-value-of-31-characters!!"
    assert len(short) == 31
    with pytest.raises(ValidationError) as caught:
        _load(monkeypatch, JWT_SECRET=short)
    assert short not in str(caught.value)


def test_settings_accept_a_32_char_jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    _load(monkeypatch, JWT_SECRET="k" * 32)  # secrets-check: allow


def test_a_secret_is_never_echoed_when_another_setting_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = "v" * 40
    with pytest.raises(ValidationError) as caught:
        _load(monkeypatch, JWT_SECRET=value, ACCESS_TOKEN_TTL_MINUTES="0")
    assert value not in str(caught.value)


def test_token_ttl_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACCESS_TOKEN_TTL_MINUTES", raising=False)
    monkeypatch.delenv("REFRESH_TOKEN_TTL_DAYS", raising=False)
    settings = load_settings()
    assert settings.access_token_ttl_minutes == 15
    assert settings.refresh_token_ttl_days == 30


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ACCESS_TOKEN_TTL_MINUTES", "0"),
        ("ACCESS_TOKEN_TTL_MINUTES", "1441"),
        ("REFRESH_TOKEN_TTL_DAYS", "0"),
        ("REFRESH_TOKEN_TTL_DAYS", "366"),
    ],
)
def test_token_ttl_out_of_bounds_is_rejected(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    with pytest.raises(ValidationError):
        _load(monkeypatch, **{name: value})


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ACCESS_TOKEN_TTL_MINUTES", "1"),
        ("ACCESS_TOKEN_TTL_MINUTES", "1440"),
        ("REFRESH_TOKEN_TTL_DAYS", "1"),
        ("REFRESH_TOKEN_TTL_DAYS", "365"),
    ],
)
def test_token_ttl_bounds_are_inclusive(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    _load(monkeypatch, **{name: value})

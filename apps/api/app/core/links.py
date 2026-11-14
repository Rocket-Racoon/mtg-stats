"""Shareable links (S-1.6.7, decision B6).

``public_link()`` is the only way the API builds a shareable link (invite, reset or join, and
the contents of join QR codes). Links are ``https://`` web URLs under ``PUBLIC_WEB_URL``; the
``mtgstats://`` scheme is for in-app navigation and the web page's ``OpenInAppButton`` only, and
no API response ever returns it for sharing.
"""

from typing import Final, Literal, get_args
from urllib.parse import quote

from app.core.config import Settings, get_settings

LinkKind = Literal["invite", "reset", "join"]
LINK_KINDS: Final[frozenset[str]] = frozenset(get_args(LinkKind))


def public_link(kind: LinkKind, value: str, *, settings: Settings | None = None) -> str:
    """Return ``<PUBLIC_WEB_URL>/<kind>/<value>`` with ``value`` fully percent-encoded.

    Every reserved character in ``value`` (``/``, ``?``, ``#``, spaces, non-ASCII as UTF-8) is
    encoded, so the value is always exactly one path segment. The dot segments ``.`` and ``..``
    are rejected: they stay literal under percent-encoding and browsers resolve them away
    (``%2e%2e`` too), so they would not remain a path segment. ``%`` is always encoded, so no
    other dot-segment form can occur.

    Raises:
        ValueError: ``kind`` is not ``invite``, ``reset`` or ``join``, or ``value`` is empty,
            ``.`` or ``..``.
    """
    if kind not in LINK_KINDS:
        raise ValueError(f"unknown link kind {kind!r}; expected one of {sorted(LINK_KINDS)}")
    if not value:
        raise ValueError("link value must not be empty")
    if value in {".", ".."}:
        raise ValueError("link value must not be a dot segment ('.' or '..')")
    base = (settings or get_settings()).public_web_url
    return f"{base}/{kind}/{quote(value, safe='')}"

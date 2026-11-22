"""``Retry-After`` parsing shared by the outbound HTTP clients (S-3.1.2, S-4.2.1)."""

import math
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime


def _utc_now() -> datetime:
    return datetime.now(UTC)


def parse_retry_after(
    value: str | None, *, default: float, now: Callable[[], datetime] | None = None
) -> float:
    """``Retry-After`` in seconds (RFC 9110: delay-seconds or an HTTP-date).

    A number waits that long (a negative one waits 0). A date waits ``max(0, date - now())``. A
    missing, unparseable or non-finite value returns ``default``. ``now`` must be timezone-aware.
    """
    if value is None:
        return default
    text = value.strip()
    try:
        seconds = float(text)
    except ValueError:
        pass
    else:
        if not math.isfinite(seconds):
            return default
        return max(0.0, seconds)
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return default
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - (now or _utc_now)()).total_seconds())

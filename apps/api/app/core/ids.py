"""UUID v7 generator (RFC 9562), standard library only (decision B1).

Layout: 48-bit Unix time in milliseconds, 4 version bits, a 12-bit counter, 2 variant bits and
62 random bits. The counter makes IDs generated in the same millisecond (in one process) sort in
creation order; if it overflows, the timestamp is advanced by one millisecond.

Ordering is per process and at millisecond granularity: IDs from different processes or workers
in the same millisecond have no defined order. If the clock goes backwards, ``_last_ms`` holds the
highest timestamp seen, so IDs keep increasing (RFC 9562 allows this). After ``fork`` the child
resets the lock and state, so it never inherits a lock held by another thread.
"""

import os
import threading
import time
import uuid

_COUNTER_MAX = 0xFFF
_lock = threading.Lock()
_last_ms = -1
_counter = 0


def _reset_after_fork() -> None:
    global _lock, _last_ms, _counter  # noqa: PLW0603
    _lock = threading.Lock()
    _last_ms = -1
    _counter = 0


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_after_fork)


def uuid7() -> uuid.UUID:
    """Return a new UUID v7 that sorts after every UUID v7 this process generated before."""
    global _last_ms, _counter  # noqa: PLW0603
    with _lock:
        ms = time.time_ns() // 1_000_000
        if ms > _last_ms:
            _last_ms = ms
            _counter = int.from_bytes(os.urandom(2), "big") & 0x3FF  # headroom before overflow
        else:
            _counter += 1
            if _counter > _COUNTER_MAX:
                _last_ms += 1
                _counter = 0
        ms, counter = _last_ms, _counter
    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
    value = (ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= counter << 64
    value |= 0b10 << 62
    value |= rand_b
    return uuid.UUID(int=value)

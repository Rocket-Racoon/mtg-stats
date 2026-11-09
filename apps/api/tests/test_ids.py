"""Unit tests for the UUID v7 generator."""

import time
import uuid

import pytest

import app.core.ids as ids_module
from app.core.ids import uuid7


def test_uuid7_sets_version_and_variant_bits() -> None:
    value = uuid7()
    assert isinstance(value, uuid.UUID)
    assert value.version == 7
    assert value.variant == uuid.RFC_4122


def test_uuid7_embeds_current_unix_millis() -> None:
    before = time.time_ns() // 1_000_000
    value = uuid7()
    after = time.time_ns() // 1_000_000 + 1  # the counter may borrow one millisecond
    assert before <= value.int >> 80 <= after


def test_uuid7_sorts_in_creation_order_within_one_millisecond() -> None:
    ids = [uuid7() for _ in range(20_000)]
    assert ids == sorted(ids)
    assert [i.bytes for i in ids] == sorted(i.bytes for i in ids)
    assert len(set(ids)) == len(ids)


def test_uuid7_counter_overflow_advances_the_timestamp(monkeypatch: pytest.MonkeyPatch) -> None:
    # Patch the generator state too, so monkeypatch restores it and nothing leaks to other tests.
    monkeypatch.setattr(ids_module, "_last_ms", ids_module._last_ms)
    monkeypatch.setattr(ids_module, "_counter", ids_module._counter)
    frozen = ids_module._last_ms + 10_000_000
    monkeypatch.setattr(ids_module.time, "time_ns", lambda: frozen * 1_000_000)
    ids = [uuid7() for _ in range(5000)]
    assert ids == sorted(ids)
    assert ids[-1].int >> 80 > frozen

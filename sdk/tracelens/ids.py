"""OpenTelemetry-compatible identifier generation.

Trace ids are 128-bit, span ids 64-bit, both rendered lowercase hex (D-005).
Tests need reproducible ids, so generation goes through a swappable generator
rather than calling ``secrets`` directly at each site.
"""

from __future__ import annotations

import random
import re
import secrets
import threading
from collections.abc import Iterator
from contextlib import contextmanager

TRACE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
SPAN_ID_PATTERN = re.compile(r"^[0-9a-f]{16}$")

INVALID_TRACE_ID = "0" * 32
INVALID_SPAN_ID = "0" * 16


class IdGenerator:
    """Cryptographically random ids. The default generator."""

    def trace_id(self) -> str:
        return secrets.token_hex(16)

    def span_id(self) -> str:
        return secrets.token_hex(8)


class SeededIdGenerator(IdGenerator):
    """Reproducible ids for tests and for seeded benchmark runs.

    Not for production use: the ids are predictable by construction.
    """

    def __init__(self, seed: int) -> None:
        self._random = random.Random(seed)

    def trace_id(self) -> str:
        return f"{self._random.getrandbits(128):032x}"

    def span_id(self) -> str:
        return f"{self._random.getrandbits(64):016x}"


_lock = threading.Lock()
_generator: IdGenerator = IdGenerator()


def new_trace_id() -> str:
    with _lock:
        return _generator.trace_id()


def new_span_id() -> str:
    with _lock:
        return _generator.span_id()


@contextmanager
def deterministic_ids(seed: int = 0) -> Iterator[SeededIdGenerator]:
    """Make all id generation reproducible for the duration of the block."""
    global _generator
    generator = SeededIdGenerator(seed)
    with _lock:
        previous, _generator = _generator, generator
    try:
        yield generator
    finally:
        with _lock:
            _generator = previous


def is_valid_trace_id(value: str) -> bool:
    return bool(TRACE_ID_PATTERN.match(value)) and value != INVALID_TRACE_ID


def is_valid_span_id(value: str) -> bool:
    return bool(SPAN_ID_PATTERN.match(value)) and value != INVALID_SPAN_ID

"""The exporter contract."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import Trace


@runtime_checkable
class Exporter(Protocol):
    """Receives finished traces.

    Implementations must not raise for recoverable conditions; the tracer
    catches exceptions, but an exporter that throws on every trace turns every
    pipeline run into a logged warning storm.
    """

    def export(self, trace: Trace) -> None: ...

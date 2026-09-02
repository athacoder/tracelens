"""In-process exporter. Used by tests, the benchmark, and the examples."""

from __future__ import annotations

from ..models import Trace


class MemoryExporter:
    """Keeps every exported trace in a list.

    The default exporter, so an un-configured Tracer still records something
    useful rather than silently discarding work or failing to construct.
    """

    def __init__(self) -> None:
        self.traces: list[Trace] = []

    def export(self, trace: Trace) -> None:
        self.traces.append(trace)

    @property
    def last(self) -> Trace | None:
        return self.traces[-1] if self.traces else None

    def clear(self) -> None:
        self.traces.clear()

    def __len__(self) -> int:
        return len(self.traces)

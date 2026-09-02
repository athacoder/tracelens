"""JSONL file exporter.

Lets the benchmark and the demo generate traces with no server running, and
gives the ingestion API a replayable input file.
"""

from __future__ import annotations

from pathlib import Path

from ..models import Trace


class FileExporter:
    """Appends one JSON object per line."""

    def __init__(self, path: str | Path, append: bool = True) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not append and self.path.exists():
            self.path.unlink()

    def export(self, trace: Trace) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(trace.model_dump_json() + "\n")

    def read_all(self) -> list[Trace]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as handle:
            return [Trace.model_validate_json(line) for line in handle if line.strip()]

"""Ships finished traces to a TraceLens backend."""

from __future__ import annotations

import logging

from ..client import TraceLensClient
from ..models import Trace

logger = logging.getLogger("tracelens")


class HttpExporter:
    """POSTs each finished trace to ``/api/v1/traces``.

    Export happens inline on trace completion. That is a deliberate v1 choice:
    a background queue is only worth its failure modes once ingestion latency
    is actually measured to matter, and the tracer already isolates the caller
    from export faults.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        client: TraceLensClient | None = None,
    ) -> None:
        self.client = client or TraceLensClient(base_url=base_url, api_key=api_key)

    def export(self, trace: Trace) -> None:
        self.client.send_trace(trace)

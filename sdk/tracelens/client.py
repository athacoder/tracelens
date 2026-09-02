"""HTTP client for the TraceLens backend.

Used by :class:`tracelens.exporters.http.HttpExporter` to ship traces, and
usable directly to read traces and forensic reports back out.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from .models import Trace

DEFAULT_TIMEOUT = 10.0


class TraceLensClient:
    """A thin wrapper over the v1 API.

    The API key, when present, is sent as a bearer token and is never written
    into a trace, a log line, or an error message.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = (
            base_url or os.getenv("TRACELENS_API_URL") or "http://localhost:8000"
        ).rstrip("/")
        self._api_key = api_key or os.getenv("TRACELENS_API_KEY") or None
        self._timeout = timeout
        self._client = client

    @property
    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        if self._api_key:
            headers["authorization"] = f"Bearer {self._api_key}"
        return headers

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = f"{self.base_url}/api/v1{path}"
        if self._client is not None:
            response = self._client.request(method, url, headers=self._headers, **kwargs)
        else:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.request(method, url, headers=self._headers, **kwargs)
        response.raise_for_status()
        return response

    # -- writes ----------------------------------------------------------

    def send_trace(self, trace: Trace) -> dict[str, Any]:
        """Ingest a complete trace, spans and events included."""
        response = self._request("POST", "/traces", content=trace.model_dump_json())
        return response.json()

    # -- reads -----------------------------------------------------------

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health").json()

    def get_trace(self, trace_id: str) -> Trace:
        return Trace.model_validate(self._request("GET", f"/traces/{trace_id}").json())

    def list_traces(self, **params: Any) -> dict[str, Any]:
        clean = {k: v for k, v in params.items() if v is not None}
        return self._request("GET", "/traces", params=clean).json()

    def get_spans(self, trace_id: str) -> list[dict[str, Any]]:
        return self._request("GET", f"/traces/{trace_id}/spans").json()

    def get_failures(self, trace_id: str) -> list[dict[str, Any]]:
        return self._request("GET", f"/traces/{trace_id}/failures").json()

    def get_root_cause_report(self, trace_id: str) -> dict[str, Any]:
        return self._request("GET", f"/traces/{trace_id}/root-cause").json()

    def get_pipeline_health(self, **params: Any) -> dict[str, Any]:
        clean = {k: v for k, v in params.items() if v is not None}
        return self._request("GET", "/pipelines/health", params=clean).json()

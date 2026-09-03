/**
 * The dashboard's only route to the backend.
 *
 * Every page is a server component, so these run on the server. That matters
 * for the base URL: inside Docker the server reaches the API at `http://api:8000`
 * on the compose network, while a browser on the host would need
 * `http://localhost:8000`. `API_INTERNAL_URL` covers the first,
 * `NEXT_PUBLIC_API_BASE_URL` the second, and locally they are the same value.
 *
 * Nothing here throws on a failed request. A dashboard whose overview page
 * crashes because the API is restarting is worse than one that says the API is
 * unreachable, so every call returns a result the page can render.
 */

import type {
  FailureBreakdownItem,
  FailureCandidate,
  HealthResponse,
  Overview,
  PipelineHealth,
  RootCauseReport,
  SpanSummary,
  TraceDetail,
  TraceListResponse,
} from "./types";

export const API_BASE_URL =
  process.env.API_INTERNAL_URL ??
  process.env.NEXT_PUBLIC_API_BASE_URL ??
  "http://localhost:8000";

export type ApiResult<T> =
  | { ok: true; data: T }
  | { ok: false; error: string; status: number | null };

async function request<T>(path: string, init?: RequestInit): Promise<ApiResult<T>> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/v1${path}`, {
      ...init,
      // Trace data changes constantly and the dashboard is a debugging tool;
      // a cached failure list is a misleading failure list.
      cache: "no-store",
      headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
    });

    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        error:
          response.status === 404
            ? "Not found."
            : `The API returned ${response.status} ${response.statusText}.`,
      };
    }
    return { ok: true, data: (await response.json()) as T };
  } catch (error) {
    return {
      ok: false,
      status: null,
      error:
        `Could not reach the TraceLens API at ${API_BASE_URL}. ` +
        `Start it with: uvicorn app.main:app --reload` +
        (error instanceof Error ? ` (${error.message})` : ""),
    };
  }
}

export const getHealth = () => request<HealthResponse>("/health");

export const getOverview = (project?: string) =>
  request<Overview>(`/overview${project ? `?project=${encodeURIComponent(project)}` : ""}`);

export const getPipelineHealth = (project?: string) =>
  request<PipelineHealth[]>(
    `/pipelines/health${project ? `?project=${encodeURIComponent(project)}` : ""}`,
  );

export const getFailureBreakdown = (project?: string) =>
  request<FailureBreakdownItem[]>(
    `/failures/breakdown${project ? `?project=${encodeURIComponent(project)}` : ""}`,
  );

export function listTraces(params: Record<string, string | number | boolean | undefined> = {}) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") query.set(key, String(value));
  }
  const suffix = query.toString();
  return request<TraceListResponse>(`/traces${suffix ? `?${suffix}` : ""}`);
}

export const getTrace = (traceId: string) => request<TraceDetail>(`/traces/${traceId}`);

export const getSpans = (traceId: string) => request<SpanSummary[]>(`/traces/${traceId}/spans`);

export const getFailures = (traceId: string) =>
  request<FailureCandidate[]>(`/traces/${traceId}/failures`);

export const getRootCause = (traceId: string) =>
  request<RootCauseReport>(`/traces/${traceId}/root-cause`);

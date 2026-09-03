# Architecture

How TraceLens is put together, and why each piece is where it is.

## Layers

```text
sdk/tracelens/          the domain model + instrumentation. No backend imports.
        │
        ├── models.py           Trace, Span, Event, Stage, SpanStatus, Severity
        ├── ids.py              OTel-shaped ids, seedable for tests
        ├── redaction.py        capture policy: full / redacted / metadata
        ├── tracing/            Tracer, Scope, contextvars propagation
        ├── exporters/          memory, JSONL file, HTTP
        └── client.py           typed client over the v1 API

backend/app/            everything that reads a trace rather than producing one.
        │
        ├── evaluation/         lexical primitives + the 5 evaluators
        ├── detection/          8 detectors, the failure taxonomy, payload conventions
        ├── invariants/         declarable rules + registry + explanations
        ├── forensics/          dependencies → divergence → scoring → report → semantic
        ├── storage/            SQLAlchemy tables, mapping, repository
        ├── services/           ingestion, analysis, replay
        ├── schemas/            HTTP response shapes
        ├── api/                the v1 router
        └── core/               settings

benchmark/              a broken RAG pipeline with ground truth, and its metrics.
frontend/               Next.js dashboard, server components only.
```

The dependency direction is strict and one-way: `backend` imports `tracelens`,
never the reverse. The SDK can be installed and used by a pipeline that has
never heard of the backend.

## The one model rule (D-004)

`tracelens.models` is the single definition of what a trace is. The backend
imports it as its API request schema, the forensic engine consumes it, and the
SDK produces it.

The alternative — a Pydantic model in the SDK and a parallel one in the backend
— is the standard way producer and consumer drift apart one field at a time.
The cost of avoiding it is one mapping module (`storage/mapping.py`) that knows
both the domain model and the ORM rows; nothing else in the codebase holds both
shapes at once.

Storage rows are genuinely a different concern and stay separate: they carry
denormalised columns (`duration_ms`, `sequence`) that exist for indexing and
ordering, not for the wire contract.

## Two tiers of validation

The model distinguishes states that can never be legitimate from states that
merely look wrong:

| Raises | Reported by `structural_errors()` |
|---|---|
| duplicate span id | span references a missing parent |
| parent cycle | child starts before its parent |
| span ends before it starts | child ends after its parent |
| span claiming another trace | span left open in a finished trace |

The split matters because during streaming ingestion a child can legitimately
arrive before its parent, and a partially recorded trace should still be
storable and analysable. Refusing to store data you already have is worse than
storing it with a note.

## The forensic pipeline

```text
trace
  ├─→ run_detectors()      8 independent detectors → FailureCandidate[]
  ├─→ run_invariants()     declared rules          → InvariantViolation[]
  │                                                   ↓ .to_candidate()
  └─→ find_first_divergence(trace, candidates)
           │  direct_dependencies()  nesting → data flow → sequence
           │  attach candidates to spans, walk in execution order
           ↓
      DivergenceReport   each span labelled: healthy / root cause /
           │             downstream consequence / unrelated anomaly
           ↓
      rank_failure_candidates()   base × position × agreement × impact
           ↓
      generate_root_cause_report()  evidence chain (incriminating +
           │                        exculpatory + consequence), remediation
           ↓
      analyse_semantically()      optional; explains, never overrules
```

Every step before the last is a pure function with no network access. That is
what makes the benchmark reproducible and what lets the whole engine be tested
without a database, a server, or an API key.

## Storage

Six tables: `traces`, `spans`, `events`, `failures`, `evaluations`,
`root_cause_reports`.

`projects` and `pipelines` are derived by grouping rather than being tables
(D-009) — neither has an attribute beyond its name, so a table would add two
joins to every trace query and buy nothing. The composite indexes on
`(project, start_time)` and `(pipeline, start_time)` make the grouped queries
cheap.

Indexes follow the queries the dashboard actually issues: filter, then sort by
time. Hence composites rather than single-column indexes on each field.

SQLAlchemy against `DATABASE_URL` (D-001): SQLite by default so tests and CI
need no service, PostgreSQL 16 in the container stack. The same Alembic
migrations run on both, verified in CI on both, with `render_as_batch` so
SQLite's inability to `ALTER` in place does not diverge the two.

## Ingestion and analysis

Analysis runs inline with ingestion (D-003). One trace is a bounded in-memory
pass; the measured cost is on every report as `analysis_ms` and is 4.7 ms mean
across the 112-case benchmark. A queue would add a service to operate and a
source of eventual consistency in the dashboard to remove 5 ms from a request.

The measurement lives in the repository, so the day that stops being true, the
evidence to revisit the decision already exists.

`ANALYSE_ON_INGEST=false` turns it off for bulk backfill;
`POST /traces/{id}/analyse` re-runs it, which is what lets a diagnosis produced
by an older detector set be replaced without re-running the pipeline.

## API

`/api/v1` — ingestion (`traces`, `spans`, `events`), reads, forensics
(`failures`, `root-cause`, `analyse`, `semantic`), aggregates (`overview`,
`pipelines/health`, `failures/breakdown`), and `health`.

Endpoints stay thin: validate, delegate, shape a response. Anything that
reasons about a trace lives in `forensics`; anything that queries lives in
`storage.repository`. Both stay testable without HTTP, and both are.

Ingestion reuses the domain model as its request schema rather than defining a
parallel one. A model rejection maps to 422, not 500 — a span that ends before
it starts is a bad request, and the caller needs to see which rule broke.

## Frontend

Next.js App Router, server components throughout, one stylesheet, no UI
framework. The dashboard is a dense information-first developer tool; a
component library would bring a consumer-app design language and a dependency
to keep current in exchange for tokens that fit in one file.

The API client never throws. A dashboard whose overview crashes because the API
is restarting is worse than one that says the API is unreachable, so every call
returns a result the page renders.

Two base URLs, because server components run server-side: `API_INTERNAL_URL`
for inside the compose network, `NEXT_PUBLIC_API_BASE_URL` for the browser.
Locally they are the same value.

## What was deliberately not built

Recorded in [DECISIONS.md](../DECISIONS.md) with reasons: no vector database
(D-002), no Redis or workers (D-003, D-010), no `projects`/`pipelines` tables
(D-009), no OpenTelemetry SDK dependency despite OTel-compatible data (D-005).

Each was considered against a requirement that does not yet exist. Each entry
records the evidence that would justify revisiting it.

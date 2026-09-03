# Architecture Decisions

Decisions are recorded when made, with the reason and the date. They are not
revised retroactively; superseding decisions get a new entry.

---

## D-001 — SQLAlchemy with a SQLite default, PostgreSQL for the container stack
**Date:** 2026-09-02
**Decision:** Persistence goes through SQLAlchemy 2.0 ORM against `DATABASE_URL`.
The default is local SQLite; `docker compose` supplies PostgreSQL 16.
**Reason:** CLAUDE.md §11 names PostgreSQL, and the compose stack provides it. But
tests and CI must run with no external service (§16: "Use mocks/local fixtures for
the default CI path"). SQLAlchemy makes the storage layer portable, so the same
code and the same migrations serve both. Nothing in the schema is Postgres-specific.

## D-002 — No pgvector, no vector database
**Date:** 2026-09-02
**Decision:** Semantic evaluation uses deterministic lexical measures (token overlap,
numeric/entity extraction, claim-to-source grounding) rather than embeddings.
**Reason:** CLAUDE.md §11 says pgvector "where vector functionality is useful" and
§10 puts deterministic checks before LLM judgment. The forensic question — did the
retrieved context actually support the answer — is answered more defensibly and
reproducibly by grounding checks than by a cosine score. Adding a vector store would
add infrastructure without improving the benchmark. Revisit if grounding recall
proves inadequate against ground truth.

## D-003 — No Redis, no background workers in v1
**Date:** 2026-09-02
**Decision:** Forensic analysis runs synchronously inside the ingestion API request
path and is also callable offline.
**Reason:** CLAUDE.md §11 says "Use background workers only where they are justified"
and §26 forbids premature infrastructure. Analysis of one trace is a pure in-memory
pass over a bounded span list; measured latency is reported in the benchmark. A queue
becomes justified when analysis latency exceeds request budget, not before.

## D-004 — One canonical domain model, owned by the SDK
**Date:** 2026-09-02
**Decision:** `tracelens.models` (Pydantic) is the single definition of Trace, Span,
Event, Status, and Error. The backend imports it for its API schemas and keeps a
separate, thin SQLAlchemy layer for storage only.
**Reason:** CLAUDE.md §12 shows models in both `sdk/` and `backend/app/models/`.
Two independent definitions of the wire format is the classic source of drift between
producer and consumer. Pydantic model = domain and wire contract; ORM model = rows.
The mapping between them lives in one place (`app.storage.mapping`).

## D-005 — OpenTelemetry-compatible vocabulary, not an OTel dependency
**Date:** 2026-09-02
**Decision:** Traces, spans, events, attributes, status, and parent/child links follow
OpenTelemetry semantics (128-bit trace id, 64-bit span id, hex encoding, nanosecond
timestamps expressed as UTC datetimes). The `opentelemetry-sdk` package is not a
dependency.
**Reason:** CLAUDE.md §11 asks for OTel-compatible concepts and forbids inventing
incompatible ones. Compatibility is a data-shape property; taking the SDK dependency
would buy exporters we do not use and constrain the forensic attributes we do need.
An OTel exporter can be added later without changing the model.

## D-006 — Deterministic-first forensics; the LLM layer is optional and mockable
**Date:** 2026-09-02
**Decision:** Detection, invariants, first-divergence, and ranking are pure functions
with no network access. The LLM forensic layer consumes their output as evidence and
is disabled by default (`TRACELENS_LLM_PROVIDER=mock`).
**Reason:** CLAUDE.md §10.3, §29, and §16. Benchmark numbers must be reproducible and
must not depend on a paid API. The measured accuracy reported in the README is the
deterministic engine's, so it can be reproduced by anyone who clones the repository.

## D-007 — Build the forensic core before the API and database
**Date:** 2026-09-02
**Decision:** Implement phases 5-8 (detection, invariants, first-divergence,
scoring) before phases 3-4 (ingestion API, persistence), then build the API once
with its forensic endpoints already present.
**Reason:** CLAUDE.md §13 lists the phases in a different order, and §44 permits
deviation that materially improves the work. The forensic engine is pure functions
over the Phase 1 model and depends on nothing from the API or the database, while
the API's `/failures` and `/root-cause` endpoints depend entirely on it. Building
the API first would mean writing it twice. Each phase still lands as its own
tested commit, so the increment size is unchanged. The phase acceptance criteria
are unchanged.

## D-008 — Detectors report calibrated-by-construction confidence, not certainty
**Date:** 2026-09-02
**Decision:** Every detector returns a `confidence` in [0, 1] whose meaning is
fixed by how the evidence was obtained: 1.0 only for directly observed facts (a
span carries an exception), 0.5-0.8 for deterministic rule violations, below 0.5
for heuristics with no baseline (latency without history).
**Reason:** CLAUDE.md §5 requires detectors to be able to say confidence = 0.61
rather than pretend certainty, and §8 forbids calling an uncalibrated score a
probability. Anchoring the number to evidence provenance makes it comparable
across detectors, which is what ranking in Phase 8 needs, without claiming a
statistical calibration the project has not done.

## D-009 — Projects and pipelines are derived, not tables
**Date:** 2026-09-02
**Decision:** `projects` and `pipelines` are computed by grouping the `traces`
table on its `project` and `pipeline` columns rather than existing as their own
tables with foreign keys.
**Reason:** CLAUDE.md §4 lists both among the tables the schema should support,
and §26 forbids complexity that is not yet justified. Neither entity currently has
a single attribute beyond its name — no owner, no retention policy, no settings —
so a table would add two joins to every trace query and buy nothing. The composite
indexes on `(project, start_time)` and `(pipeline, start_time)` make the grouped
queries cheap. Promote them to real tables the moment either grows an attribute.

## D-010 — No Redis or worker service in the container stack
**Date:** 2026-09-03
**Decision:** `docker-compose.yml` runs three services at most: PostgreSQL, the API,
and the web UI. No Redis, no queue, no worker.
**Reason:** Follows D-003. Forensic analysis of one trace is a bounded in-memory
pass, and the measured cost is reported on every report as `analysis_ms` — 4.8 ms
mean across the 112-case benchmark. A queue would add a service to operate, a
failure mode to debug, and a source of eventual consistency in the dashboard, in
exchange for removing 5 ms from a request. The measurement is in the repository,
so the day it stops being true the evidence to revisit this will already exist.

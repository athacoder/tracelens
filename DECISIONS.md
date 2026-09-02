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

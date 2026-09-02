# TASKS

Status: `todo` | `doing` | `done` | `blocked`
Evidence means a verified command output, not an assertion.

| ID | Description | Priority | Status | Depends on | Tests required | Evidence |
|----|-------------|----------|--------|------------|----------------|----------|
| T-000 | Repo + toolchain bootstrap (Phase 0) | P0 | doing | — | health check | — |
| T-001 | Trace/Span/Event domain model (Phase 1) | P0 | todo | T-000 | unit: nesting, ordering, validation, round-trip | — |
| T-002 | Python instrumentation SDK (Phase 2) | P0 | todo | T-001 | unit: decorator, ctx manager, exceptions, exporter | — |
| T-003 | Trace ingestion API (Phase 3) | P0 | todo | T-001 | api: happy path, malformed, 404, pagination | — |
| T-004 | Persistence + migrations (Phase 4) | P0 | todo | T-003 | integration: round-trip through DB | — |
| T-005 | Failure detection engine (Phase 5) | P0 | todo | T-001 | unit per detector | — |
| T-006 | Invariant engine (Phase 6) | P0 | todo | T-001 | unit: pass, fail, multi-violation, severity | — |
| T-007 | First-divergence engine (Phase 7) | P0 | todo | T-005, T-006 | unit: hand-built traces per scenario | — |
| T-008 | Root-cause scoring + report (Phase 8) | P0 | todo | T-007 | unit: ranking order, confidence bounds | — |
| T-009 | Semantic forensic layer + provider abstraction (Phase 9) | P1 | todo | T-008 | unit: mock provider, schema validation | — |
| T-010 | Broken RAG benchmark + ground truth (Phase 10) | P0 | todo | T-002 | scenario fixtures load and run | — |
| T-011 | Failure injection framework (Phase 11) | P0 | todo | T-010 | determinism under fixed seed | — |
| T-012 | Benchmark metrics + report (Phase 12) | P0 | todo | T-011, T-008 | metric computation unit tests | — |
| T-013 | Replay engine (Phase 13) | P1 | todo | T-004 | unit: compare_runs diff | — |
| T-014 | Frontend dashboard (Phase 14) | P1 | todo | T-003 | build passes | — |
| T-015 | Forensic report UI (Phase 15) | P1 | todo | T-014, T-008 | build passes | — |
| T-016 | CI workflow (Phase 16) | P0 | todo | T-005 | workflow runs lint+tests | — |
| T-017 | Docker compose stack (Phase 17) | P1 | todo | T-004 | compose config validates | — |
| T-018 | Documentation + README (Phase 18) | P0 | todo | T-012 | claims match evidence | — |
| T-019 | End-to-end acceptance flow (§36) | P0 | todo | all | one e2e test | — |
| T-020 | GitHub remote + push | P0 | blocked | user action | remote verified | `gh` not installed; no git credential helper configured |

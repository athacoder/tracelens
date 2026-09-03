# TASKS

Status: `todo` | `doing` | `done` | `blocked`
Evidence means a verified command output, not an assertion.

| ID | Description | Priority | Status | Depends on | Tests required | Evidence |
|----|-------------|----------|--------|------------|----------------|----------|
| T-000 | Repo + toolchain bootstrap (Phase 0) | P0 | done | — | health check | `pytest -q` runs; initial commit 3316681 |
| T-001 | Trace/Span/Event domain model (Phase 1) | P0 | done | T-000 | unit: nesting, ordering, validation, round-trip | 46 unit tests in sdk/tests/test_models.py |
| T-002 | Python instrumentation SDK (Phase 2) | P0 | done | T-001 | unit: decorator, ctx manager, exceptions, exporter | 59 unit tests in sdk/tests/test_tracer.py |
| T-003 | Trace ingestion API (Phase 3) | P0 | done | T-001 | api: happy path, malformed, 404, pagination | 50 tests in backend/tests/test_api.py |
| T-004 | Persistence + migrations (Phase 4) | P0 | done | T-003 | integration: round-trip through DB | 35 tests in backend/tests/test_storage.py; alembic up/down verified |
| T-005 | Failure detection engine (Phase 5) | P0 | done | T-001 | unit per detector | 82 tests in backend/tests/test_detectors.py + test_evaluators.py |
| T-006 | Invariant engine (Phase 6) | P0 | done | T-001 | unit: pass, fail, multi-violation, severity | 36 tests in backend/tests/test_invariants.py |
| T-007 | First-divergence engine (Phase 7) | P0 | done | T-005, T-006 | unit: hand-built traces per scenario | 39 tests in backend/tests/test_forensics.py |
| T-008 | Root-cause scoring + report (Phase 8) | P0 | done | T-007 | unit: ranking order, confidence bounds | ranking + report covered in test_forensics.py |
| T-009 | Semantic forensic layer + provider abstraction (Phase 9) | P1 | done | T-008 | unit: mock provider, schema validation | 31 tests in backend/tests/test_semantic_and_replay.py |
| T-010 | Broken RAG benchmark + ground truth (Phase 10) | P0 | done | T-002 | scenario fixtures load and run | 14 scenarios with ground truth in benchmark/scenarios.py |
| T-011 | Failure injection framework (Phase 11) | P0 | done | T-010 | determinism under fixed seed | `python -m benchmark.run --scenario X`; seeded ids reproducible |
| T-012 | Benchmark metrics + report (Phase 12) | P0 | done | T-011, T-008 | metric computation unit tests | 112 cases; report in benchmark/reports/latest.txt |
| T-013 | Replay engine (Phase 13) | P1 | done | T-004 | unit: compare_runs diff | replay + compare_runs, covered in test_semantic_and_replay.py |
| T-014 | Frontend dashboard (Phase 14) | P1 | done | T-003 | build passes | frontend build passes; verified against a live API |
| T-015 | Forensic report UI (Phase 15) | P1 | done | T-014, T-008 | build passes | forensic report screen verified in the browser |
| T-016 | CI workflow (Phase 16) | P0 | done | T-005 | workflow runs lint+tests | `.github/workflows/ci.yml`; every step run locally first |
| T-017 | Docker compose stack (Phase 17) | P1 | done | T-004 | compose config validates | `docker compose config` validates; images NOT built (no daemon) |
| T-018 | Documentation + README (Phase 18) | P0 | done | T-012 | claims match evidence | README + 4 docs; every number re-verified before writing |
| T-019 | End-to-end acceptance flow (§36) | P0 | done | all | one e2e test | 7 tests in tests/integration/test_acceptance.py |
| T-020 | GitHub remote + push | P0 | done | user action | remote verified | pushed 2026-09-03; `git ls-remote` confirms remote main == local HEAD `685ba63` |
| T-021 | Build and run the container images | P1 | blocked | Docker daemon | `docker compose up` serves the stack | compose config validates; daemon would not start in this environment |
| T-022 | Verify CI actually passes | P0 | done | T-020 | green workflow run | run 33777838556, all 4 jobs success, incl. PostgreSQL migrations |
| T-023 | Tag v1.0.0 | P1 | todo | T-020, T-022 | tag + release | every section 35 gate now passes; awaiting a decision to tag |

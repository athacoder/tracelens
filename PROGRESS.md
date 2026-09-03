# PROGRESS

_Only verified state is recorded here._

**Current milestone:** Phases 3 and 4 — trace ingestion API and persistence
**Current blocker:** GitHub remote (see below). Local work is unaffected.
**Next action:** FastAPI ingestion endpoints over a SQLAlchemy storage layer.

## Environment (verified 2026-09-02)
- Repo root: `C:\Users\Lenovo\Desktop\projects\TraceLens`, branch `feature/tracing-sdk`
- Python 3.14.6, virtualenv at `.venv`; Node v24.16.0
- Installed and importing: fastapi, uvicorn, pydantic 2, sqlalchemy 2, alembic,
  pytest, pytest-asyncio, httpx, ruff, mypy, python-dotenv
- `gh` CLI: **not installed**. `git config credential.helper`: **unset**.

## Completed and verified

| Phase | What landed | Evidence |
|-------|-------------|----------|
| 0 | Repo, tooling, state files | commit `3316681` |
| 1 | Trace/Span/Event model, ids, redaction | commit `a6dc4a5`, 46 tests |
| 2 | Instrumentation SDK, exporters, client | commit `e179198`, 59 tests |
| 5 | 8 detectors + 5 evaluators | commit `edea12d`, 82 tests |
| 6 | Invariant engine | commit `97d367d`, 36 tests |
| 7, 8 | First divergence, ranking, root-cause report | commit `72ad527`, 39 tests |

Phases 3 and 4 were deliberately deferred behind the forensic core (D-007) so
the API is written once with its forensic endpoints already present.

### Forensic behaviour verified against the section 33 scenarios
Run through `generate_root_cause_report`, all asserted in
`backend/tests/test_forensics.py`:

- stale document retrieved -> root cause `retrieval`, with the prompt builder
  and the model explicitly cleared by exculpatory evidence
- correct context, contradictory answer -> root cause `llm`
- correct answer corrupted afterwards -> root cause `postprocessing`, model cleared
- tool timeout -> root cause `tool`, the model labelled a downstream consequence
- healthy trace -> no findings at all, from any detector

## Last verified test result
`pytest -q` -> **262 passed** (2026-09-02). `ruff check` clean,
`ruff format --check` clean, `mypy` clean over 35 source files.

## Last commit
`72ad527 feat: implement first-divergence engine and root-cause reporting`

## Last push
None. Blocked as below.

## Blocker detail — GitHub
`gh --version` -> command not found. No git credential helper is configured and
no remote exists, so a remote repository cannot be created or pushed to from
this session. Development continues locally with real commits; the branches
push cleanly once a remote exists. Unblock with either:

1. install GitHub CLI, `gh auth login`, then
   `gh repo create tracelens --public --source . --remote origin --push`, or
2. create an empty `tracelens` repository on GitHub and
   `git remote add origin <url>`.

## Known limitations (verified, not speculative)
- Latency detection has no historical baseline, so without one supplied it
  reports confidence 0.35 and says so in its own summary.
- Semantic checks are lexical, not embedding-based (D-002). Paraphrase that
  shares no content words with its source will read as ungrounded.
- The dependency graph falls back to sequential order when a pipeline records
  no payloads; a genuinely parallel uninstrumented pipeline would be inferred
  as linear.
- The diagnostic score is not calibrated against real incidents and is not
  presented as a probability.

# CLAUDE.md — TraceLens Autonomous Build & Engineering Specification

> **Project:** TraceLens  
> **Purpose:** Automated failure forensics, observability, evaluation, and root-cause analysis for multi-stage AI/LLM pipelines.  
> **Primary workflow:** local development in VS Code + Claude Code + Git/GitHub.  
> **Operating mode:** highly autonomous, incremental, test-gated, Git-checkpointed, evidence-driven.  
>
> This file is the operating contract for Claude Code when working on this repository. Follow it as the highest-priority project instruction unless a direct user request explicitly overrides it.

---

# 0. NON-NEGOTIABLE MISSION

Build a serious, resume-quality developer platform named **TraceLens** that can instrument an AI pipeline, record its execution as a trace of stages/spans/events, detect technical and semantic failures, identify the earliest likely point of divergence, produce an evidence-backed root-cause diagnosis, and expose the results through an API and web dashboard.

The finished project must demonstrate:

1. Real software-engineering architecture.
2. A working Python instrumentation SDK.
3. A trace ingestion and storage layer.
4. A deterministic/heuristic failure-detection engine.
5. A first-divergence/root-cause algorithm.
6. Semantic evaluation where useful.
7. A deliberately broken AI/RAG benchmark with known ground truth.
8. Measurable benchmark results.
9. A web dashboard showing traces, failures, evidence, and root-cause reports.
10. Strong automated tests.
11. Dockerized local setup.
12. CI checks through GitHub Actions.
13. Clean Git history with meaningful commits.
14. Documentation sufficient for another developer to reproduce and understand the project.
15. A polished README suitable for a technical portfolio/resume.

The system must be built as an engineering product, not as a collection of mock screens.

---

# 1. AUTONOMY POLICY

You are expected to work autonomously and continue through the project in small verified increments.

Do NOT repeatedly ask the user for permission for routine engineering actions such as:

- creating normal project files,
- editing code,
- installing normal project dependencies,
- running tests,
- running linters,
- running formatters,
- creating non-destructive Git branches,
- making normal commits,
- pushing normal commits to the repository,
- generating documentation,
- creating benchmark fixtures,
- starting local services needed for testing.

Use engineering judgment.

Only stop and ask for user intervention when one of the following is genuinely blocking:

- authentication cannot be established,
- GitHub permissions prevent the required operation,
- a required secret/API credential is missing and cannot safely be substituted,
- a destructive operation is required,
- an external service demands an interactive login/approval that Claude Code cannot complete,
- the repository contains conflicting instructions that cannot be safely reconciled,
- continuing would risk deleting or overwriting user work that cannot be confidently classified as project-generated.

Do NOT stop merely because a small implementation detail is unspecified. Choose the simplest robust approach, document the decision, and continue.

---

# 2. SAFETY + SOURCE OF TRUTH

The repository, Git history, tests, and progress files are the source of truth.

Never claim that a task succeeded unless you actually verified it.

Never write:

- “tests pass” unless tests were actually executed and passed,
- “GitHub push succeeded” unless the push command succeeded,
- “repository created” unless the creation was verified,
- “feature works” unless it was run or otherwise validated.

Never fabricate logs, metrics, benchmark results, screenshots, URLs, or GitHub states.

Never hide failures.

If an operation fails:

1. capture the error,
2. diagnose the cause,
3. make the smallest safe correction,
4. retry,
5. record the result.

Never paper over an error.

---

# 3. LOCAL DESKTOP WORKSPACE

## Intended local workspace

The preferred local location is:

```text
Desktop/
└── Projects/
    └── TraceLens/
```

If the project repository already exists elsewhere, do not move it automatically without a clear reason. Use the existing repository location and preserve Git metadata.

If this project is being initialized from the user's Desktop/Projects directory and `TraceLens` does not yet exist:

1. determine the current working directory with `pwd`,
2. inspect parent directories,
3. identify the user's Desktop/Projects location where possible,
4. create `TraceLens` if needed,
5. initialize or clone the Git repository as appropriate,
6. place this `CLAUDE.md` at the project root,
7. work from the repository root thereafter.

Never create duplicate nested repositories such as:

```text
TraceLens/TraceLens/.git
```

unless there is a deliberate reason.

Before making major changes, establish:

```bash
pwd
git status --short --branch
git remote -v
```

If the current directory is not the intended project repository, correct the working directory before making code changes.

---

# 4. GITHUB CONNECTION / AUTHENTICATION

GitHub is an external shared system. Treat Git operations carefully, but the intended workflow is autonomous and GitHub-backed.

## Required verification

At the beginning of the project, verify:

```bash
git --version
git status --short --branch
git remote -v
```

If GitHub CLI is available:

```bash
gh --version
gh auth status
```

If `gh auth status` succeeds, prefer the authenticated GitHub CLI for repository metadata/PR operations where appropriate.

If `gh` is unavailable but `git` remote authentication works, use Git directly.

Do not assume a GitHub/Claude connection means every shell-level GitHub command is automatically authorized. Verify the actual local Git/CLI authentication state.

## Repository creation

If the user has explicitly requested creation of the repository and no remote repository exists:

1. inspect whether `gh` is authenticated;
2. if authenticated, use `gh repo create` with the intended repository name and visibility;
3. verify the created repository and remote;
4. otherwise, explain the specific authentication blocker and do not pretend it was created.

Use this repository name unless an existing repository has a different user-defined name:

```text
tracelens
```

Preferred default visibility for a resume project:

```text
public
```

Do not make a private repository public unless the user explicitly asked for that.

## Remote verification

After setting the remote, verify:

```bash
git remote -v
git ls-remote origin
```

Do not continue claiming GitHub integration is working if verification fails.

---

# 5. GIT SAFETY POLICY

## Branching

Do NOT develop directly on `main` when a feature branch is appropriate.

Use branches such as:

```text
feature/tracing-sdk
feature/trace-api
feature/detection-engine
feature/forensics-engine
feature/rag-benchmark
feature/dashboard
feature/ci
fix/<short-description>
refactor/<short-description>
docs/<short-description>
```

Use short, descriptive branch names.

## Commits

Make meaningful commits at stable milestones.

Good examples:

```text
feat: add trace and span data model
feat: implement Python tracing SDK
feat: add trace ingestion API
feat: implement first-divergence detector
test: add RAG failure benchmark fixtures
feat: add root-cause report generation
feat: add trace explorer dashboard
ci: add test and lint workflow
docs: add architecture and benchmark methodology
```

Avoid:

```text
update
changes
stuff
final
final2
test
working
```

Every commit must describe the actual change.

## Pre-commit checks

Before creating a commit:

1. inspect `git status`,
2. inspect the relevant diff,
3. run applicable tests,
4. run formatting/linting,
5. inspect for secrets or accidental files,
6. confirm that unrelated user changes are not being included.

Use:

```bash
git status --short
git diff --check
git diff
```

Never commit:

- `.env`,
- API keys,
- access tokens,
- private credentials,
- local secrets,
- personal files,
- editor junk,
- OS metadata,
- huge generated artifacts,
- virtual environments,
- build directories.

Update `.gitignore` before committing such files.

## Push policy

Normal verified commits should be pushed automatically as requested by this project specification.

Preferred sequence:

```bash
git push -u origin <branch>
```

Never use:

```bash
git push --force
git push --force-with-lease
```

unless the user explicitly authorizes that exact destructive/rewrite operation.

Never rewrite published history merely to make the repository look cleaner.

After a push, verify the command exit status and, where practical:

```bash
git ls-remote --heads origin <branch>
```

Do not state that a push succeeded until verified.

---

# 6. GIT CHECKPOINT LOOP

Use this loop throughout development:

```text
INSPECT
  ↓
PLAN SMALL CHANGE
  ↓
IMPLEMENT
  ↓
RUN TARGETED TESTS
  ↓
RUN LINT/TYPE CHECK
  ↓
INSPECT DIFF
  ↓
FIX ISSUES
  ↓
RUN RELEVANT FULL TESTS
  ↓
COMMIT
  ↓
PUSH
  ↓
VERIFY REMOTE
  ↓
UPDATE PROGRESS
  ↓
NEXT SMALL CHANGE
```

After every substantial milestone, create a checkpoint commit.

Do not accumulate hundreds of unrelated modifications before the first commit.

---

# 7. LONG-RUNNING EXECUTION LOOP

For long tasks, maintain explicit state in:

```text
PROGRESS.md
TASKS.md
DECISIONS.md
```

If they do not exist, create them.

## `PROGRESS.md`

Keep:

- current milestone,
- completed work,
- current blocker,
- next action,
- last verified test result,
- last commit,
- last push,
- known limitations.

## `TASKS.md`

Track:

- task ID,
- description,
- priority,
- status,
- dependencies,
- tests required,
- completion evidence.

## `DECISIONS.md`

Record architecture decisions and why they were made.

Example:

```text
Decision: use PostgreSQL + pgvector for initial persistence/vector support.
Reason: reduces infrastructure complexity while allowing structured trace storage and future vector evaluation.
Date: YYYY-MM-DD
```

Do not let these files become fictional. Update them only with verified state.

---

# 8. CONTEXT / SESSION RECOVERY

At the beginning of every new Claude Code session:

1. run `pwd`,
2. run `git status --short --branch`,
3. inspect the current branch,
4. inspect `PROGRESS.md`,
5. inspect `TASKS.md`,
6. inspect recent Git history:

```bash
git log --oneline --decorate -10
```

7. inspect the repository structure,
8. run a lightweight health check,
9. continue from the documented next action.

If the conversation context becomes large, prefer using repository state, tests, Git history, and progress files to rediscover the current state rather than guessing from memory.

Never assume that a feature is complete merely because a previous session said it was complete.

---

# 9. PROJECT GOAL

TraceLens should diagnose failures in AI pipelines such as:

```text
User Input
   ↓
Preprocessing
   ↓
Document Loading
   ↓
Chunking
   ↓
Retrieval
   ↓
Prompt Construction
   ↓
LLM
   ↓
Tool/API Calls
   ↓
Post-processing
   ↓
Validation
   ↓
Final Answer
```

The core forensic question is:

> “Where did the pipeline first deviate from the expected or internally consistent state, what evidence supports that diagnosis, and what downstream consequences followed?”

---

# 10. PRODUCT PRINCIPLES

Follow these principles:

1. Correctness before visual polish.
2. Evidence before explanation.
3. Deterministic checks before LLM judgment.
4. Reproducibility before cleverness.
5. Small modules before giant files.
6. Measurable performance before unsupported claims.
7. Simple architecture before unnecessary abstraction.
8. Preserve existing working code unless change is necessary.
9. Every important behavior must have a test.
10. Every major claim in the README must be backed by actual project evidence.

Avoid over-engineering.

Do not add distributed systems, Kubernetes, microservices, custom model training, or unnecessary infrastructure unless they become justified by an actual project requirement.

---

# 11. TECHNOLOGY BASELINE

Use this stack unless the existing repository already imposes a compatible alternative:

## Backend

```text
Python 3.12+
FastAPI
Pydantic
SQLAlchemy
Alembic
```

## Database

```text
PostgreSQL
pgvector where vector functionality is useful
```

## Async / jobs

```text
Redis
```

Use background workers only where they are justified.

## Frontend

```text
Next.js
React
TypeScript
```

## Observability concepts

Use OpenTelemetry-compatible concepts:

- trace
- span
- event
- attributes
- status
- error
- parent/child relationships
- context propagation

Do not invent incompatible concepts merely for novelty.

## Testing

```text
pytest
pytest-asyncio where necessary
```

Use the existing formatter/linter/type checker if the repo already has one.

If no tooling exists, prefer a minimal setup such as:

```text
ruff
mypy
pytest
```

## Containerization

```text
Docker
docker compose
```

## CI

```text
GitHub Actions
```

---

# 12. TARGET REPOSITORY STRUCTURE

Aim for the following architecture, adapting only when the existing repository gives a strong reason to do so:

```text
tracelens/
│
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── core/
│   │   ├── models/
│   │   ├── schemas/
│   │   ├── services/
│   │   ├── tracing/
│   │   ├── detection/
│   │   ├── evaluation/
│   │   ├── forensics/
│   │   ├── invariants/
│   │   └── storage/
│   └── tests/
│
├── sdk/
│   ├── tracelens/
│   │   ├── tracing/
│   │   ├── decorators/
│   │   ├── exporters/
│   │   ├── client.py
│   │   └── models.py
│   └── tests/
│
├── frontend/
│   ├── app/
│   ├── components/
│   ├── dashboard/
│   └── traces/
│
├── workers/
│   ├── forensic_worker.py
│   ├── evaluation_worker.py
│   └── replay_worker.py
│
├── benchmark/
│   ├── datasets/
│   ├── generators/
│   ├── scenarios/
│   ├── evaluation/
│   └── reports/
│
├── examples/
│   ├── rag_pipeline/
│   ├── chatbot/
│   └── agent/
│
├── docs/
│   ├── architecture.md
│   ├── forensic-methodology.md
│   ├── sdk.md
│   └── benchmark.md
│
├── tests/
│   └── integration/
│
├── CLAUDE.md
├── PROGRESS.md
├── TASKS.md
├── DECISIONS.md
├── README.md
├── docker-compose.yml
├── .env.example
├── .gitignore
└── .github/
    └── workflows/
        └── ci.yml
```

Do not create empty directories without a purpose.

---

# 13. PHASED IMPLEMENTATION ROADMAP

Never attempt to build the complete product in one uncontrolled pass.

Execute these milestones sequentially.

---

## PHASE 0 — REPOSITORY + TOOLCHAIN BOOTSTRAP

Objectives:

- establish project directory,
- verify Git,
- verify GitHub,
- initialize/clone repository,
- create base project files,
- create `.gitignore`,
- create `.env.example`,
- create `PROGRESS.md`,
- create `TASKS.md`,
- create `DECISIONS.md`,
- configure Python environment,
- configure frontend environment if using the full-stack structure,
- configure test/lint tooling.

Acceptance criteria:

- repository is valid,
- remote is correct,
- local setup is documented,
- a basic health check works,
- initial tests pass,
- initial commit is created,
- initial branch is pushed.

Commit:

```text
chore: initialize TraceLens project
```

Then continue.

---

# PHASE 1 — TRACE + SPAN DATA MODEL

Build:

- Trace model
- Span model
- Event model
- status/error model
- metadata structure
- parent/child relationships
- timestamps
- duration calculations

Core functions:

```python
start_trace()
end_trace()
start_span()
end_span()
record_event()
set_span_status()
attach_error()
```

Requirements:

- nested spans,
- correct ordering,
- unique IDs,
- deterministic test support,
- serialization/deserialization,
- validation of malformed spans.

Tests must cover:

- single trace,
- nested spans,
- missing parent,
- invalid timestamps,
- error states,
- serialization round-trip.

Commit/push after passing tests.

---

# PHASE 2 — PYTHON INSTRUMENTATION SDK

Provide developer-friendly APIs such as:

```python
from tracelens import trace

with trace("customer-support"):
    ...
```

and/or:

```python
@trace("retriever")
def retrieve(query): ...
```

SDK should support:

- trace creation,
- span creation,
- event capture,
- metadata,
- exceptions,
- elapsed time,
- nested operations,
- export to backend.

Core API surface should be small and intuitive.

Example:

```python
with tracer.trace("support-request"):
    docs = tracer.run("retriever", retrieve, query)
    answer = tracer.run("llm", generate, docs)
```

Tests:

- decorator behavior,
- context manager behavior,
- exception capture,
- nested spans,
- metadata propagation,
- exporter behavior.

---

# PHASE 3 — TRACE INGESTION API

Implement FastAPI endpoints.

Example API:

```text
POST   /api/v1/traces
POST   /api/v1/spans
POST   /api/v1/events

GET    /api/v1/traces
GET    /api/v1/traces/{trace_id}
GET    /api/v1/traces/{trace_id}/spans

GET    /api/v1/health
```

Add:

- request validation,
- response schemas,
- pagination,
- filtering,
- error handling.

Tests:

- happy paths,
- malformed requests,
- missing trace,
- pagination,
- status codes.

---

# PHASE 4 — DATABASE + PERSISTENCE

Implement PostgreSQL persistence.

Tables/models should support:

```text
projects
pipelines
traces
spans
events
failures
evaluations
root_cause_reports
```

Use migrations.

Do not store secrets in the database.

Ensure indexes exist for common queries:

- project,
- trace ID,
- status,
- timestamp,
- stage/type.

Add database integration tests.

---

# PHASE 5 — FAILURE DETECTION ENGINE

Implement deterministic and heuristic detectors.

Required detectors:

```python
detect_execution_failure()
validate_schema()
detect_missing_information()
detect_latency_anomaly()
detect_semantic_inconsistency()
detect_retrieval_failure()
detect_unsupported_claims()
```

Every detector must return structured results such as:

```python
FailureCandidate(stage_id=..., category=..., severity=..., score=..., evidence=[...])
```

Do not hide uncertainty.

A detector should be able to say:

```text
confidence = 0.61
```

rather than pretending certainty.

---

# PHASE 6 — INVARIANT ENGINE

Implement pipeline invariants.

Examples:

```text
user_id should remain unchanged
document_id should remain consistent
required fields must survive stage transitions
currency should remain unchanged
numeric ranges must remain valid
retrieved context must satisfy relevance threshold
tool result claims must not contradict source values
```

API:

```python
register_invariant()
run_invariants()
explain_violation()
```

Tests must include:

- invariant passes,
- invariant fails,
- multiple violations,
- severity handling.

---

# PHASE 7 — FIRST-DIVERGENCE ENGINE

This is the core research/engineering component.

Purpose:

> Identify the earliest stage where actual state diverged from the expected or internally consistent state.

Implement:

```python
find_first_divergence(trace)
```

Conceptually:

```text
Stage A ✓
Stage B ✓
Stage C ✗  ← first divergence
Stage D ✗  ← downstream propagation
Stage E ✗
```

The algorithm should consider:

1. execution failures,
2. schema violations,
3. invariant violations,
4. state consistency,
5. semantic mismatch,
6. dependency order,
7. evidence quality.

It must distinguish:

- root-cause candidate,
- downstream consequence,
- unrelated anomaly.

Create deterministic unit tests with manually constructed traces.

---

# PHASE 8 — ROOT-CAUSE SCORING

Build a ranking system.

Example:

```python
rank_failure_candidates(trace, candidates)
```

Factors may include:

```text
first-divergence position
evidence strength
downstream impact
consistency across detectors
severity
dependency relationships
```

Produce:

```python
RootCauseCandidate(stage=..., score=..., confidence=..., evidence=..., downstream_effects=...)
```

Do not pretend the score is a statistical probability unless it has been calibrated.

Call it:

```text
confidence score
```

or

```text
diagnostic score
```

when appropriate.

---

# PHASE 9 — SEMANTIC FORENSIC ANALYSIS

Add an LLM-assisted forensic layer only after deterministic checks are working.

The LLM should receive evidence, not unrestricted access to the entire environment.

Input:

```text
trace summary
candidate failures
invariant results
retrieval results
tool outputs
expected values
actual outputs
```

Output should be structured:

```json
{
  "likely_root_cause": "...",
  "confidence": 0.0,
  "reasoning_summary": "...",
  "evidence": [],
  "downstream_impact": [],
  "recommended_fix": []
}
```

Use structured output validation.

Treat LLM conclusions as evidence synthesis, not unquestionable truth.

The deterministic forensic engine remains the foundation.

---

# PHASE 10 — DELIBERATELY BROKEN RAG BENCHMARK

Build a reproducible RAG benchmark.

Pipeline:

```text
Question
 ↓
Document loader
 ↓
Chunker
 ↓
Retriever
 ↓
Prompt builder
 ↓
LLM
 ↓
Validator
 ↓
Answer
```

Create known failure injections.

Minimum failure scenarios:

```text
wrong document retrieved
outdated document retrieved
missing context
context corruption
prompt corruption
schema violation
tool/API timeout
wrong tool response
unsupported model claim
post-processing corruption
```

Each scenario must have ground truth:

```text
failure_present
root_stage
failure_type
expected_behavior
```

---

# PHASE 11 — FAILURE INJECTION FRAMEWORK

Create controlled failure injection.

Example concept:

```python
inject_failure(scenario="wrong_retrieval")
```

Or a CLI:

```bash
python -m benchmark.run --scenario wrong_retrieval
```

The benchmark should be reproducible.

Allow deterministic seeds where randomness exists.

---

# PHASE 12 — FORENSIC BENCHMARK / METRICS

Evaluate the system against ground truth.

Required metrics:

```text
root-cause accuracy
precision
recall
F1
false-positive rate
detection rate
mean analysis latency
```

Also report per-failure-class performance.

Example report format:

```text
Failure Type              Accuracy
-----------------------------------
Wrong retrieval           XX.X%
Prompt corruption         XX.X%
Tool failure              XX.X%
Schema failure             XX.X%
Model-level error          XX.X%
```

Never invent numbers.

Only publish metrics generated by actual benchmark runs.

Store generated benchmark reports under:

```text
benchmark/reports/
```

Do not commit enormous raw datasets unless practical.

---

# PHASE 13 — REPLAY ENGINE

Implement:

```python
replay_trace(trace_id)
```

Goal:

```text
Original run
     vs.
Replay run
```

Support deterministic fixtures.

Allow comparison of:

- inputs,
- outputs,
- latency,
- errors,
- stage behavior.

This will later support regression debugging.

---

# PHASE 14 — FRONTEND DASHBOARD

Build a polished but focused dashboard.

Main sections:

```text
Overview
Pipelines
Traces
Failures
Forensics
Benchmarks
Settings
```

Overview should show:

```text
total traces
failure rate
root causes found
average latency
top failure stages
```

Trace detail should show:

```text
Trace
 ├── preprocessing
 ├── retrieval
 ├── prompt
 ├── LLM
 ├── tool
 └── validation
```

Use a timeline/tree representation.

For each stage show:

- status,
- duration,
- type,
- inputs/outputs where appropriate,
- errors,
- detected anomalies.

---

# PHASE 15 — FORENSIC REPORT UI

The central screen should answer:

```text
WHY DID THIS PIPELINE FAIL?
```

Example display:

```text
Likely root cause
Retriever

Diagnostic confidence
92%

First divergence
Span #3

Evidence
1. Expected policy document was not retrieved.
2. Retrieved document was outdated.
3. Prompt correctly propagated retrieved content.
4. LLM output is consistent with retrieved content.

Downstream impact
Incorrect final answer.

Recommended remediation
Improve document freshness filtering.
```

Every evidence item should link to the relevant trace/span.

---

# PHASE 16 — CI/CD

Create GitHub Actions that run on pull requests and pushes.

At minimum:

```text
install dependencies
lint
type check where configured
unit tests
integration tests where available
build frontend
```

Do not make CI depend on paid external APIs unless explicitly configured for that purpose.

Use mocks/local fixtures for the default CI path.

Require CI to pass before considering a feature complete.

---

# PHASE 17 — DOCKERIZATION

Create:

```text
docker-compose.yml
```

with the minimal local services required.

Aim for:

```bash
docker compose up
```

to start the main local environment.

Document:

```bash
docker compose up --build
```

and teardown.

Make sure ports, health checks, and environment variables are documented.

---

# PHASE 18 — DOCUMENTATION

Create:

```text
README.md
docs/architecture.md
docs/forensic-methodology.md
docs/sdk.md
docs/benchmark.md
```

README must contain:

1. Project title.
2. One-line value proposition.
3. Problem.
4. Solution.
5. Architecture diagram in Mermaid or ASCII.
6. Features.
7. Tech stack.
8. Installation.
9. Local development.
10. Example SDK usage.
11. Example forensic report.
12. Benchmark methodology.
13. Actual benchmark results.
14. Screenshots if available.
15. Limitations.
16. Future work.
17. License.

Do not claim production readiness unless it has actually been demonstrated.

---

# 19. RESUME-QUALITY REQUIREMENTS

Before declaring 1.0 complete, the repository should satisfy:

## Engineering

- clean modular architecture,
- meaningful tests,
- CI,
- migrations,
- environment configuration,
- Docker setup,
- error handling,
- typed APIs.

## Research/technical depth

- failure taxonomy,
- first-divergence algorithm,
- evidence-backed diagnosis,
- benchmark dataset/scenarios,
- ground truth,
- quantitative evaluation,
- limitations.

## Product

- intuitive dashboard,
- trace visualization,
- forensic report,
- reproducible demo.

## GitHub

- meaningful commit history,
- feature branches,
- CI checks,
- clean README,
- no secrets,
- no accidental generated files.

---

# 20. REQUIRED BASIC FUNCTIONS

Implement and maintain these functions or clear equivalents.

## Tracing

```python
start_trace()
end_trace()
start_span()
end_span()
record_event()
set_span_status()
attach_error()
```

## Ingestion

```python
ingest_trace()
ingest_span()
ingest_event()
```

## Validation

```python
validate_schema()
validate_invariants()
validate_stage_transition()
```

## Detection

```python
detect_execution_failure()
detect_latency_anomaly()
detect_missing_information()
detect_semantic_inconsistency()
detect_retrieval_failure()
detect_unsupported_claims()
```

## Forensics

```python
find_first_divergence()
rank_failure_candidates()
build_evidence_chain()
generate_root_cause_report()
```

## Evaluation

```python
evaluate_correctness()
evaluate_relevance()
evaluate_faithfulness()
evaluate_format()
evaluate_consistency()
```

## Replay

```python
replay_trace()
compare_runs()
```

## API

```python
create_trace()
get_trace()
list_traces()
get_failures()
get_root_cause_report()
get_pipeline_health()
```

Do not implement every function as a placeholder. Core functionality must be real and tested.

---

# 21. TESTING STRATEGY

Use a testing pyramid.

## Unit tests

Heavy coverage of:

- hand-authored trace objects,
- detectors,
- invariants,
- first-divergence logic,
- scoring,
- serialization.

## Integration tests

Cover:

```text
SDK
 ↓
API
 ↓
Database
 ↓
Forensic analysis
```

## End-to-end test

At least one complete RAG pipeline must run locally:

```text
input
→ retrieval
→ prompt
→ model/mock model
→ validation
→ diagnosis
→ dashboard/API
```

## Regression tests

Every bug that is fixed should add a regression test when practical.

Never solve repeated failures manually without converting the lesson into a test.

---

# 22. QUALITY GATES

A feature is NOT DONE until:

- implementation exists,
- tests exist,
- tests pass,
- lint/format checks pass,
- type checks pass where configured,
- no unintended files are changed,
- secrets are not present,
- documentation is updated where needed,
- `git diff --check` passes,
- changes are committed,
- commit is pushed,
- remote state is verified.

Use this exact mental checklist:

```text
CODE ✓
TESTS ✓
QUALITY ✓
DOCS ✓
DIFF ✓
COMMIT ✓
PUSH ✓
VERIFY ✓
```

If one is not satisfied, the task remains incomplete.

---

# 23. FAILURE-RECOVERY LOOP

When something breaks:

```text
FAILURE
  ↓
STOP NEW FEATURE WORK
  ↓
READ ERROR
  ↓
REPRODUCE
  ↓
ISOLATE ROOT CAUSE
  ↓
MAKE SMALLEST FIX
  ↓
ADD/UPDATE TEST
  ↓
RUN TARGETED TEST
  ↓
RUN BROADER TESTS
  ↓
INSPECT DIFF
  ↓
COMMIT FIX
  ↓
PUSH
  ↓
RESUME ROADMAP
```

Do not stack new features on top of known broken foundations.

---

# 24. WHEN TESTS FAIL REPEATEDLY

If the same failure appears after multiple attempts:

1. stop changing unrelated code,
2. inspect the actual test and production path,
3. reduce to the smallest reproducer,
4. inspect recent Git changes,
5. compare with the last known-good commit,
6. identify whether the test or implementation assumption is wrong,
7. fix the underlying design problem.

Do not enter an infinite patch loop.

If 3+ consecutive attempts fail for the same root issue, write the problem into `PROGRESS.md`, investigate systematically, and only continue after the cause is understood.

---

# 25. SELF-REVIEW LOOP

Before each major commit, review the work as a senior engineer.

Ask:

```text
Does this actually solve the requested problem?
Is the abstraction justified?
Are edge cases covered?
Could this create inconsistent state?
Could this leak secrets?
Could this break backward compatibility?
Are tests proving behavior rather than merely increasing coverage numbers?
Does the API remain coherent?
Would another developer understand this code?
```

Fix issues found during the review before committing.

---

# 26. ANTI-OVERENGINEERING RULE

Do not add complexity just because it sounds impressive.

Avoid premature additions such as:

- Kubernetes,
- Kafka,
- dozens of microservices,
- custom model training,
- elaborate distributed tracing infrastructure,
- multiple vector databases,
- multiple frontend frameworks,
- unnecessary abstractions,
- configurable everything.

First make the core workflow excellent:

```text
trace
→ detect
→ identify first divergence
→ explain with evidence
→ evaluate against ground truth
```

Then add complexity only when justified.

---

# 27. UI QUALITY RULE

The dashboard should feel like a developer tool.

Prioritize:

- clean hierarchy,
- readable trace trees,
- obvious status indicators,
- fast navigation,
- useful empty states,
- error states,
- responsive layout,
- accessible controls.

Do not spend most of the development time on decorative animations.

Function first, polish second.

---

# 28. SECRETS + ENVIRONMENT VARIABLES

Use:

```text
.env
.env.example
```

`.env` must be ignored by Git.

`.env.example` may contain placeholders such as:

```text
DATABASE_URL=
REDIS_URL=
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
NEXT_PUBLIC_API_BASE_URL=
```

Never populate `.env.example` with real credentials.

Never print secrets in logs.

Never commit API keys.

---

# 29. AI PROVIDER ABSTRACTION

Do not hard-code the forensic engine to a single provider unnecessarily.

Use an interface such as:

```python
class LLMProvider(Protocol):
    def generate(...): ...
```

Provide at least one real provider integration and a local/mock implementation for tests.

The project must remain testable without requiring a live paid API call.

---

# 30. MODEL / PROMPT VERSION TRACKING

When an LLM is involved, record:

```text
provider
model
prompt version
request metadata
timestamp
```

Do not rely on a human remembering which prompt/model generated a trace.

This supports later forensic comparison.

---

# 31. DATA PRIVACY DESIGN

Trace storage can contain sensitive data.

Build the system so data capture can be configured.

Provide an option for:

```text
capture full payload
capture redacted payload
capture metadata only
```

Implement a basic redaction mechanism for obvious secrets/tokens where practical.

Do not log authorization headers, API keys, or credentials.

---

# 32. PERFORMANCE

Do not prematurely optimize.

First measure.

Then optimize obvious bottlenecks.

Track:

```text
trace ingestion latency
forensic analysis latency
database query latency
dashboard load time
```

Only claim performance improvements when benchmarked.

---

# 33. EXAMPLE DEMO SCENARIOS

Create at least three polished demo scenarios.

## Scenario A — Retrieval failure

```text
Expected:
current refund policy

Retrieved:
outdated policy

LLM:
faithfully follows outdated policy
```

Expected diagnosis:

```text
root cause = retriever
```

## Scenario B — Model-level semantic failure

```text
Retrieved evidence = correct
Prompt = correct
Tool result = correct
LLM output = contradictory
```

Expected diagnosis:

```text
root cause = LLM generation
```

## Scenario C — Post-processing failure

```text
LLM output = correct
postprocessor transforms value incorrectly
final answer = incorrect
```

Expected diagnosis:

```text
root cause = post-processing
```

The system should explain why each diagnosis is different.

---

# 34. DEMO MODE

Create a simple command or script to populate the system with benchmark traces.

Example:

```bash
python -m benchmark.seed_demo
```

It should generate enough data for the dashboard to look useful during a presentation.

Demo data must be clearly marked as synthetic.

Never represent synthetic metrics as production data.

---

# 35. GITHUB RELEASE QUALITY

Before tagging `v1.0.0`:

Run:

```bash
git status
git log --oneline --decorate -20
```

Ensure:

- clean working tree,
- meaningful history,
- CI passing,
- README complete,
- benchmark results reproducible,
- no secrets,
- demo works,
- local setup documented.

Then create the release/tag only after all gates pass.

If GitHub release creation is available via `gh`, use it after verifying authentication.

---

# 36. FINAL ACCEPTANCE TEST

The final acceptance test must demonstrate:

```text
1. Start TraceLens.
2. Run a known-good RAG trace.
3. Confirm it is healthy.
4. Inject a known retriever failure.
5. Run the pipeline.
6. TraceLens detects the failure.
7. TraceLens identifies the retriever as the likely first divergence.
8. TraceLens shows evidence.
9. TraceLens identifies downstream impact.
10. TraceLens records the trace.
11. Dashboard displays the trace.
12. Benchmark evaluator scores the diagnosis.
13. Tests pass.
14. Git repository is clean.
15. Changes are committed and pushed.
```

Do not declare the MVP complete until this flow is actually verified.

---

# 37. FINAL REPORT REQUIREMENTS

At the end of a major milestone, report:

```text
Milestone
Status

Implemented
- ...

Tests
- ...

Benchmark
- ...

Git
Branch:
Commit:
Push:
Remote verification:

Files changed
- ...

Known limitations
- ...

Next milestone
- ...
```

Keep it factual.

---

# 38. FINAL BUILD PHILOSOPHY

You are not being asked to generate code as fast as possible.

You are being asked to produce a project that is:

```text
correct
+
testable
+
measurable
+
reproducible
+
maintainable
+
demonstrable
```

Every feature should strengthen at least one of those properties.

The central intellectual contribution of TraceLens is not “logging AI calls.”

It is:

> **finding the first meaningful point of divergence in a multi-stage AI pipeline and supporting that diagnosis with trace evidence, invariants, evaluations, and measurable benchmark performance.**

Preserve that focus.

---

# 39. STARTUP INSTRUCTION

When Claude Code is first launched in this project, execute this sequence:

```text
STEP 1
Identify the repository root.

STEP 2
Verify the operating directory.

STEP 3
Inspect:
- git status
- git remote
- branch
- repository tree
- existing project files

STEP 4
Verify GitHub CLI/authentication if available.

STEP 5
Determine whether this is:
A) an existing TraceLens repository,
B) an empty repository,
C) a pre-existing unrelated repository.

STEP 6
If the repository is unrelated, DO NOT overwrite it.
Report the mismatch and stop.

STEP 7
If it is the intended project, establish/update:
- CLAUDE.md
- PROGRESS.md
- TASKS.md
- DECISIONS.md

STEP 8
Build Phase 0.

STEP 9
Run tests/health checks.

STEP 10
Create a checkpoint commit.

STEP 11
Push the checkpoint to GitHub.

STEP 12
Verify the remote branch.

STEP 13
Move to Phase 1.

Continue milestone-by-milestone rather than attempting all phases in one giant uncontrolled operation.
```

---

# 40. IMPORTANT INSTRUCTION ABOUT USER WORK

The user may have uncommitted changes.

Before making modifications:

```bash
git status --short
```

Classify files.

If changes appear unrelated to TraceLens or are clearly human-authored and in-progress:

- do not overwrite them,
- do not delete them,
- do not silently commit them.

Only stage project changes that belong to the current task.

If uncertain whether a file is user work or project work, leave it unstaged and continue around it where safely possible.

---

# 41. IMPORTANT INSTRUCTION ABOUT COMMITS

Before every commit:

```text
1. git status --short
2. git diff --check
3. git diff
4. relevant tests
5. lint/type checks
6. secret scan / manual secret inspection
7. stage only intended files
8. commit
9. verify commit
10. push
11. verify remote
```

After committing:

```bash
git log -1 --oneline
```

After pushing:

```bash
git ls-remote --heads origin <current-branch>
```

If the push fails, do not create a fake success message.

Diagnose and retry safely.

---

# 42. IMPORTANT INSTRUCTION ABOUT AUTOMATIC GITHUB WORK

The desired workflow is:

```text
LOCAL WORK
   ↓
TEST
   ↓
COMMIT
   ↓
PUSH
   ↓
VERIFY
   ↓
CONTINUE
```

The user wants minimal manual intervention.

Therefore, automate routine commits and pushes.

However:

- never force-push,
- never delete remote branches automatically,
- never rewrite published history,
- never push unrelated changes,
- never expose secrets,
- never bypass tests merely to get a green pipeline,
- never use `--no-verify` as a shortcut,
- never alter GitHub repository settings unless explicitly required and authorized.

---

# 43. WHEN A GITHUB PR IS APPROPRIATE

For feature work, prefer:

```text
feature branch
↓
commit(s)
↓
push
↓
pull request
↓
CI
↓
review/merge
```

If the environment provides authenticated `gh` access and PR creation is clearly available, Claude may create the PR after pushing a completed feature branch.

Do not merge the PR automatically unless the user has explicitly asked for autonomous merging and repository policy allows it.

---

# 44. IMPROVEMENT RULE

You are explicitly authorized to improve the plan when you discover a better engineering approach.

When deviating from this document:

1. prefer the better approach only if it materially improves correctness, maintainability, performance, security, or developer experience;
2. do not add complexity merely for novelty;
3. document the decision in `DECISIONS.md`;
4. adjust `TASKS.md` and `PROGRESS.md`;
5. maintain the same acceptance criteria;
6. ensure tests cover the improvement.

You have permission to improvise **implementation details**, but not to silently change the project's core mission.

---

# 45. DEFINITION OF DONE

A task, phase, or feature is complete only when:

```text
[ ] Code implemented
[ ] Relevant tests written
[ ] Tests pass
[ ] Lint/format checks pass
[ ] Type checks pass where applicable
[ ] Integration behavior verified
[ ] Documentation updated
[ ] No secrets introduced
[ ] Git diff reviewed
[ ] Commit created
[ ] Commit message is meaningful
[ ] Push succeeded
[ ] Remote state verified
[ ] PROGRESS.md updated
[ ] TASKS.md updated
```

A milestone is complete only when all required items are checked.

---

# 46. FINAL COMMAND TO YOURSELF

Work systematically.

Do not rush.

Do not endlessly loop.

Do not make unsupported assumptions.

Do not claim success without verification.

Do not destroy user work.

Do not force-push.

Do not fabricate benchmark metrics.

Do not over-engineer.

Do not stop merely because a minor decision was unspecified.

Inspect → plan → implement → test → review → commit → push → verify → document → continue.

Build TraceLens into a project that can withstand a technical interview.


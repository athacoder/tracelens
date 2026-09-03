# Benchmark

A deliberately broken RAG pipeline with known ground truth, and the metrics
that score the forensic engine against it.

```bash
python -m benchmark.run --all                       # full grid, writes reports/
python -m benchmark.run --scenario outdated_document
python -m benchmark.run --all --seed 7 --json
python -m benchmark.seed_demo --reset --questions 3 # fill a database for the UI
```

## What makes it a measurement

Two rules, both enforced by tests.

**The pipeline is instrumented the way a user would instrument theirs.** It
goes through the public SDK with no privileged channel to the forensic engine.
The engine sees exactly what it would see in production.

**The injections break the pipeline, they do not annotate it.** No span payload
contains the scenario name — `test_the_trace_does_not_reveal_the_scenario_to_the_engine`
asserts it. The retriever simply returns a different document, and the engine
has to work out the rest.

The ground truth records what the injection *did*, never what TraceLens is
*expected to say* about it. Writing the expected diagnosis into the fixture
would make the benchmark grade itself.

## The corpus

Eight hand-written support documents (`benchmark/corpus.py`), including two
superseded ones alongside their replacements. Small on purpose: every document
and expected answer is auditable, so any reported accuracy can be checked
against the fixture by reading it.

Superseded documents are what make stale retrieval realistic rather than
synthetic — the retriever picks a real document from the real index, and
nothing downstream can tell it is wrong except its metadata.

Eight questions, each with the document that answers it, the superseded
counterpart a stale retriever would pick, and a plausible-but-unsupported
answer for the hallucination scenario.

## The pipeline

```text
question → preprocess → document load → chunk → retrieve → tool (extract)
         → build prompt → LLM → post-process → validate → answer
```

Nine spans per run. The model is a **deterministic extractive stand-in**: it
returns the sentence of its context that best matches the question. A real
model would make the benchmark unreproducible and require a paid API on every
run, which section 16 forbids for the default path. What is being measured is
the forensic engine, not a model's fluency.

The extractive model has a useful side effect: it cannot hallucinate on its
own, so anything wrong with an answer came from upstream or from a deliberate
injection.

## Scenarios

Ten single-fault classes (the section 10 minimum):

| Scenario | Injected at | Ground-truth root |
|---|---|---|
| `wrong_document` | retriever returns a real, current, off-topic document | retrieval |
| `outdated_document` | retriever returns the superseded version | retrieval |
| `missing_context` | retriever returns nothing | retrieval |
| `context_corruption` | chunker alters a number while splitting | chunking |
| `prompt_corruption` | prompt builder drops the retrieved context | prompt_build |
| `schema_violation` | retriever emits documents as a string | retrieval |
| `tool_timeout` | extraction tool raises | tool |
| `wrong_tool_response` | tool returns a value its input does not contain | tool |
| `unsupported_claim` | model asserts a number absent from its prompt | llm |
| `postprocessing_corruption` | formatter rewrites a number | postprocessing |

Plus a **harder tier**, because the ten above each break exactly one stage —
a fair test of the detectors but a weak test of the discrimination logic that
is the point of the project:

| Scenario | Competing signal | Ground-truth root |
|---|---|---|
| `stale_retrieval_with_slow_model` | latency is the louder signal | retrieval |
| `compound_retrieval_and_postprocessing` | two real faults in one run | retrieval (earliest) |
| `slow_but_correct` | a slow stage, nothing actually wrong | none — healthy |

The last one matters most: a tool that confuses slow with broken fails it.

Plus `healthy`, the control.

## Metrics

Two questions are scored separately, because conflating them flatters the
result.

**Detection** — did TraceLens notice anything was wrong? Binary, scored with
precision, recall, and F1 over *every* case including the healthy controls.

**Localisation** — given that something was wrong, did it name the right stage?
Scored only over cases where a failure was injected, because "correctly
identified the root cause of a run that had no failure" is not a thing.

A tool that flags everything scores perfect recall and useless precision. A
tool that flags nothing scores perfect precision and no recall. Reporting both,
plus the false-positive rate on healthy runs, is what makes the number mean
something.

Also reported: mean/median/p95 analysis latency, per-class accuracy, and mean
confidence when correct versus when wrong — if the engine is confident exactly
when it is right, the confidence number is worth reading; if not, the report
says so.

## Results

Run on 2026-09-03 with `python -m benchmark.run --all`. Full output in
`benchmark/reports/latest.txt`, machine-readable in `latest.json`.

**112 cases — 96 injected failures, 16 healthy controls**

| Metric | Result |
|---|---|
| Root-cause accuracy | 100.0% (96/96) |
| Precision | 100.0% |
| Recall / detection rate | 100.0% |
| F1 | 1.000 |
| False-positive rate | 0.0% (0/16) |
| Mean analysis latency | 4.70 ms |
| Median / p95 | 4.28 ms / 6.86 ms |
| Mean confidence when correct | 0.70 |
| Mean confidence when wrong | — (no wrong cases) |

All fourteen scenarios scored 100% detection and 100% localisation.

### How to read this

**A perfect score says as much about the benchmark as about the engine.** The
benchmark, the injections, and the engine share an author; the corpus is eight
documents; and each injection breaks a stage in a way some detector was built
to see.

Read it as a **regression suite** proving the engine still separates the cases
it was designed to separate. It is not evidence about production pipelines,
paraphrased failures, or faults nobody anticipated. The same caveat is printed
at the bottom of every generated report, so it cannot be separated from the
number by a copy-paste.

The honest version of this benchmark's value is narrower and still real: it
catches regressions, it forced three genuine detector bugs into the open (see
below), and it makes every accuracy claim in this repository reproducible by
anyone who clones it.

## What building it found

Every one was a false positive on a healthy run — the failure mode that makes a
forensics tool useless in practice.

1. **Instrumentation counted as content.** The transformation check read a
   stage's own `chunk_count` and `source_characters` as numbers it had
   invented, so every well-instrumented stage looked corrupted.
2. **The fix over-corrected.** Ignoring all standalone numbers silenced a
   genuine case — a tool returning `{"days_remaining": 12}`. In a tool result
   the numbers *are* the payload. Content is now filtered by key name.
3. **The tool invariant was too strict.** It demanded the answer quote the
   tool's number, which fires whenever the answer legitimately cites a
   different fact from the same documents. It now also requires the answer to
   be ungrounded.

A fourth bug came from the dependency graph: a span that produced no output — a
timed-out tool — could not be linked by data flow, so the stage it broke was
reported as *unrelated* rather than downstream. The stage that failed hardest
was the one most likely to be misreported.

All four have regression tests.

## Determinism

The pipeline contains no randomness, so a trace's content is fully determined
by its question and scenario. `--seed` fixes id generation via
`deterministic_ids`, which makes two runs of the same command byte-identical
rather than merely equivalent.

Asserted by `test_a_seed_makes_trace_ids_reproducible`.

## Adding a scenario

1. Add a `Scenario` to `benchmark/scenarios.py` with its `GroundTruth`:
   `failure_present`, `root_stage`, `failure_type`, `expected_behavior`.
2. Implement the injection in the relevant stage of `benchmark/pipeline.py`.
   Break the pipeline; do not annotate the trace.
3. Run `python -m benchmark.run --scenario your_scenario`.

The parametrised tests in `tests/test_benchmark.py` pick it up automatically,
including the assertions that it produces a well-formed trace, that it does not
leak its name into any span, and that it is diagnosed at its injected stage.

If it is diagnosed at the wrong stage, that is a finding about the engine, not
a reason to adjust the ground truth.

## CI

The benchmark job is a **gate, not a report**. `benchmark.run` exits non-zero
when any healthy control is flagged, so a regression that makes the engine
noisier fails the build rather than appearing in a file nobody reads. The
report is uploaded as an artifact on every run.

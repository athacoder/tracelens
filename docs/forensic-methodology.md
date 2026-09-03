# Forensic methodology

How TraceLens decides which stage to blame, and why it is allowed to say so.

## The question

Not "what is broken". A failing pipeline usually has several broken-looking
stages, and all but one of them are broken *because* of the first one. The
question is:

> Where did the pipeline first deviate from the expected or internally
> consistent state, what evidence supports that, and what followed from it?

Answering it requires separating three populations that a flat anomaly list
conflates:

- **root-cause candidate** — the earliest stage whose problem is its own
- **downstream consequence** — a stage whose problem is explained upstream
- **unrelated anomaly** — a real problem on a stage that fed nothing

## Step 1 — Detection

Eight detectors, each a pure function from a trace to candidates. They do not
consult each other and they do not rank. That independence is what makes
"three detectors independently flagged the retriever" a meaningful statement
later.

| Detector | Fires on |
|---|---|
| `detect_execution_failure` | a span reported an error |
| `validate_schema` | output missing a field the next stage needs, or wrong type |
| `detect_missing_information` | empty required payload; context dropped before the prompt |
| `detect_latency_anomaly` | a stage slow against a baseline, or dominating the trace |
| `detect_semantic_inconsistency` | output unsupported by, or contradicting, the same stage's input |
| `detect_retrieval_failure` | nothing retrieved, off-topic, stale, or the expected document missing |
| `detect_unsupported_claims` | the final answer asserts something no source supports |
| `detect_structural_anomaly` | the trace itself is incomplete |

### Confidence is anchored to evidence provenance

A detector's confidence records *how* it knows, not how sure it feels:

| Evidence kind | Weight | Confidence band | Example |
|---|---|---|---|
| `observed` | 1.00 | 1.0 | the span carries an exception |
| `rule` | 0.80 | 0.85–0.95 | a declared invariant broke; a stale document's own metadata |
| `comparison` | 0.65 | 0.6–0.8 | the answer asserts a number absent from its prompt |
| `heuristic` | 0.40 | < 0.5 | this stage is 85% of a trace, with no baseline |

Latency with no history reports `0.35` and says "no baseline to compare
against" in its own summary. That is the point: the number is comparable across
detectors precisely because it is not a self-assessment.

Confidence 1.0 on an execution failure means "this was observed", **not** "this
is the root cause". A downstream span erroring because its input was empty is
equally observed and equally not the cause.

## Step 2 — Invariants

Where a detector asks "does this look wrong?", an invariant asks "did this
pipeline break a rule its own author declared?". The second is easier to answer
and much harder to argue with.

```python
from app.invariants import InvariantRegistry, field_stable, numeric_within

registry = InvariantRegistry(
    [
        field_stable("user_id", Severity.CRITICAL),
        field_stable("currency", Severity.CRITICAL),
        numeric_within("confidence", 0.0, 1.0),
    ]
)
```

Two design points:

`field_stable` blames the span where the value **first differs** from the
initial observation, not the last span carrying it. Blaming the endpoint of a
corruption is how a diagnosis lands one stage too far downstream.

A registry runs every invariant even when one raises. A broken rule is reported
as a violation of itself rather than being allowed to abort the pass, so it
cannot hide the findings of the rules that still work.

Violations convert to candidates at confidence `0.95`, not `1.0`: the rule was
definitely broken, but a rule can itself be declared wrongly, and the engine
should not be more certain than the person who wrote it.

## Step 3 — Dependency inference

This is what separates cause from consequence. Order alone cannot: the earliest
anomaly in a trace may sit in a branch that never fed the failure.

Three signals, strongest first:

1. **Nesting** — a child span ran inside its parent.
2. **Data flow** — a value that left one span's outputs appears in another's
   inputs, either by exact match or by ≥50% content-word overlap. Strongest
   signal, and it survives reordering.
3. **Sequence** — in a linear pipeline each step consumes the previous one.
   Fallback only, so a pipeline that records its payloads gets the precise
   graph instead.

The sequential fallback applies from **both** ends. A consumer with no inferred
producers links backward; a producer with no inferred consumers links forward.
The second half exists because a span that recorded no output — a timed-out
tool — cannot be linked by data flow at all, and without it the stage that
failed hardest is the one most likely to be misreported as unrelated. That was
a real bug, found by the benchmark, and it now has a regression test.

## Step 4 — First divergence

```text
1. attach every finding to its span
2. walk spans in execution order
3. take the first whose findings include an originating category with
   weight ≥ 0.15
4. everything after it that transitively consumed its output → downstream
5. everything after it that did not → unrelated, reported separately
```

**Originating** categories can start a failure: execution error, schema
violation, missing information, semantic inconsistency, retrieval failure,
unsupported claim, invariant violation.

**Corroborating** categories cannot: latency anomaly, structural anomaly. They
are reported and they raise a candidate's score, but naming latency as the root
cause of a wrong answer is a bad diagnosis and the engine will not produce one.
An early slow stage does not outrank a later stage that actually broke.

## Step 5 — Ranking

```text
score = base × position × agreement × impact
```

| Factor | Answers | Range |
|---|---|---|
| `base` | how bad is the worst finding here (severity × confidence × evidence strength) | 0–1 |
| `position` | did this stage start the failure or inherit it | 1.0 / 0.60 / 0.35 |
| `agreement` | did independent detectors reach the same conclusion | 1.0–1.24 |
| `impact` | how much of the pipeline consumed this output | 1.0–1.20 |

Multiplied rather than summed, because the factors are not interchangeable: a
downstream stage with overwhelming evidence is still not the root cause, and no
amount of detector agreement rescues a finding with no evidence behind it.

Every factor is recorded on the candidate under `score_components`, so any
number can be taken apart and argued with.

`confidence` is a separate quantity: how much this candidate dominates the
alternatives (`score / total`), tempered by its evidence strength. A lone
finding backed only by a heuristic should not report high confidence merely
because nothing competed with it.

**Neither number is a probability.** Nothing here has been calibrated against a
population of real incidents, so calling 0.92 a 92% chance would be a lie that
happens to sound rigorous.

## Step 6 — The evidence chain

A ranked list is not a diagnosis. The chain is assembled in the order a person
reads it:

1. **Incriminating** — what the diverging stage did wrong.
2. **Exculpatory** — why each downstream stage that behaved correctly is *not*
   to blame.
3. **Consequence** — what the downstream stages that did misbehave inherited.

The second element is the one usually missing, and it is what turns a claim
into an argument. "The retriever is at fault" invites "how do you know it
wasn't the model?" — and the answer is:

```text
[clears] prompt-builder carried the retrieved content into the prompt
         unchanged, so the prompt reflects what retrieval returned
[clears] llm produced an answer consistent with the prompt it was given,
         so the model followed its evidence
```

Every evidence item carries the span it came from, so the dashboard links each
one back to the stage that produced it.

## Step 7 — The semantic layer (optional)

Runs last and consumes the deterministic output. Its entire input is a brief
assembled from findings the engine already extracted — it cannot read the
database, call the API, or see anything beyond that brief, and a test asserts
the brief contains no connection string or credential.

The deterministic engine stays authoritative. When the model names a different
stage, the result records `disagrees_with_deterministic` and marks itself not
trustworthy rather than overwriting the diagnosis: an ambiguous case is worth a
human look, not a silent substitution. Disagreement is matched on prose, so
"the retriever returned a superseded document" counts as agreeing with
`retrieval`.

A provider failure is captured on the result, not raised. Losing the narrative
must not lose the answer.

## Worked example: three wrong answers, three diagnoses

The same symptom — a wrong final answer — with three different causes.

**A. Stale retrieval.** The retriever returns the superseded policy. The prompt
carries it faithfully; the model answers from it faithfully.

- Retrieval detector: expected document absent (`rule`, 0.95) and the document's
  own metadata says superseded (`rule`, 0.85)
- The model is **not** flagged: its answer is fully grounded in its prompt
- Diagnosis: **retrieval**, with the prompt builder and model explicitly cleared

**B. Model contradiction.** Retrieval, prompt, and tool are all correct; the
answer asserts `14` where the context says `30`.

- Semantic inconsistency at the LLM: an ungrounded number (`comparison`, 0.80)
- Retrieval is clean, so nothing upstream can explain it
- Diagnosis: **LLM**

**C. Post-processing corruption.** The model was right; the formatter rewrote
the number.

- Transformation inconsistency at post-processing: `30 became 3`
- The model is explicitly **healthy**, not merely outranked
- Diagnosis: **post-processing**

All three are asserted in `backend/tests/test_forensics.py` and re-asserted
through the HTTP API in `tests/integration/test_acceptance.py`.

## Known weaknesses

- **Lexical, not semantic.** Grounding is content-word and numeric overlap
  (D-002). A paraphrase sharing no words with its source reads as ungrounded.
  Chosen for reproducibility and auditability; revisit if grounding recall
  proves inadequate against ground truth.
- **Uncalibrated scores.** Ranking within a trace only.
- **No latency history.** Without supplied baselines, latency is a weak signal
  and reports itself as one.
- **Sequential fallback.** A genuinely parallel pipeline that records no
  payloads is inferred as linear.
- **Content vs. measurement is matched by key name.** `chunk_count` and
  `prompt_characters` are treated as a stage's own instrumentation rather than
  content it carried. An unusual field name could be misclassified either way.

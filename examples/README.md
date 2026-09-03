# Examples

Runnable pipelines instrumented with the TraceLens SDK.

## `rag_pipeline/`

A four-stage RAG pipeline with a superseded document in its index.

```bash
python examples/rag_pipeline/main.py            # broken run, diagnosed locally
python examples/rag_pipeline/main.py --healthy  # control run
python examples/rag_pipeline/main.py --send     # export to a running API
```

The broken run is the interesting one. Nothing raises, every span reports `ok`,
the prompt faithfully carries what retrieval returned, and the model faithfully
answers from that prompt — and the answer is still wrong, because the retriever
returned a policy that was superseded in 2025.

TraceLens names the retriever and clears the two stages after it explicitly:

```text
  trace status : ok  (nothing raised)
  root cause   : retriever (retrieval)

  evidence:
    1. [cause ] retriever returned stale document(s): refund-2019 (superseded by refund-2026)
    2. [clears] prompt-builder carried the retrieved content into the prompt unchanged
    3. [clears] llm produced an answer consistent with the prompt it was given
```

For the full failure taxonomy across ten injected classes, see the benchmark:

```bash
python -m benchmark.run --all
```

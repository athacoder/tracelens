"""Lexical primitives the deterministic evaluators are built from.

TraceLens grounds its semantic checks in what can be observed and re-derived
from the trace itself (D-002). That means comparing the surface content of one
stage against another: which tokens, numbers, dates, and quoted identifiers
appear where. It is a weaker signal than an embedding model, and deliberately
so — every judgement it makes can be re-run by anyone with the same trace and
will produce the same answer.

The functions here are intentionally boring. The forensic value is in *which*
comparisons the detectors make, not in the cleverness of the string matching.
"""

from __future__ import annotations

import re
from typing import Any

from tracelens.redaction import segments

#: Words carrying no discriminative weight when comparing a question to a
#: document. Deliberately short: an aggressive stop list starts deleting the
#: domain terms that make a retrieval match meaningful.
STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "can",
        "did",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "our",
        "so",
        "than",
        "that",
        "the",
        "their",
        "then",
        "there",
        "these",
        "they",
        "this",
        "to",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
    ]
)

_WORD = re.compile(r"[A-Za-z][A-Za-z'-]*|\d[\d,._/-]*\d|\d")
_NUMBER = re.compile(
    r"(?<![\w.])-?\d{1,3}(?:,\d{3})*(?:\.\d+)?(?![\w])|(?<![\w.])-?\d+(?:\.\d+)?(?![\w])"
)
_DATE = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}/\d{1,2}/\d{2,4}"
    r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4})\b",
    re.IGNORECASE,
)
_CURRENCY = re.compile(
    r"[$£€¥]\s?-?\d[\d,]*(?:\.\d+)?|\b\d[\d,]*(?:\.\d+)?\s?(?:USD|EUR|GBP|INR|JPY)\b"
)


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens, punctuation dropped, numbers preserved."""
    return [match.group(0).lower() for match in _WORD.finditer(text)]


def content_words(text: str) -> set[str]:
    """Tokens that carry meaning: stopwords and single characters removed."""
    return {t for t in tokenize(text) if t not in STOPWORDS and len(t) > 1}


def numbers(text: str) -> set[str]:
    """Numeric literals, normalised so 1,200 and 1200 compare equal."""
    found = set()
    for match in _NUMBER.finditer(text):
        raw = match.group(0).replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            continue
        found.add(_format_number(value))
    return found


def _format_number(value: float) -> str:
    return str(int(value)) if value == int(value) else str(value)


def dates(text: str) -> set[str]:
    return {m.group(0).lower() for m in _DATE.finditer(text)}


def currency_amounts(text: str) -> set[str]:
    return {m.group(0).replace(" ", "").upper() for m in _CURRENCY.finditer(text)}


def overlap(left: str, right: str) -> float:
    """Fraction of ``left``'s content words that also appear in ``right``.

    Asymmetric on purpose. "Does the answer stay within the context?" and "does
    the context cover the question?" are different questions, and a symmetric
    similarity score answers neither of them well.
    """
    left_words = content_words(left)
    if not left_words:
        return 0.0
    return len(left_words & content_words(right)) / len(left_words)


def jaccard(left: str, right: str) -> float:
    """Symmetric similarity, used when neither side is the reference."""
    a, b = content_words(left), content_words(right)
    if not a and not b:
        return 1.0
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def flatten_text(value: Any, _depth: int = 0) -> str:
    """Render an arbitrary payload as searchable text.

    Trace payloads are nested dicts of documents, chunks, and tool results.
    Grounding checks need one flat string to search, and the structure is
    already available separately for the checks that care about it.
    """
    if _depth > 8:
        return ""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool | int | float):
        return str(value)
    if isinstance(value, dict):
        return " ".join(flatten_text(v, _depth + 1) for v in value.values())
    if isinstance(value, list | tuple | set):
        return " ".join(flatten_text(v, _depth + 1) for v in value)
    return str(value)


#: Key segments naming something a stage measured about its own work rather
#: than content it carried. Excluded when checking whether a value was altered
#: in transit: a stage that reports chunk_count or prompt_characters alongside
#: its output has not invented those numbers, and counting them as content
#: makes every properly instrumented stage look like it corrupted data.
#:
#: Filtered by key name rather than by type on purpose. An earlier version
#: skipped every standalone number, which silenced the genuine case of a tool
#: returning {"days_remaining": 12} — in a tool result, the numbers are the
#: payload.
MEASUREMENT_KEY_SEGMENTS = frozenset(
    {
        "bytes",
        "characters",
        "chars",
        "count",
        "duration",
        "elapsed",
        "index",
        "latency",
        "len",
        "length",
        "ms",
        "offset",
        "seq",
        "sequence",
        "size",
        "tokens",
    }
)


def is_measurement_key(key: str) -> bool:
    """Whether a field name denotes a measurement rather than content."""
    return bool(segments(key) & MEASUREMENT_KEY_SEGMENTS)


def flatten_content(value: Any, _depth: int = 0) -> str:
    """Render a payload's content, dropping fields that are self-measurements.

    Used by the checks that ask "did this stage alter a value it was handed?",
    where a stage's own instrumentation counts must not be mistaken for the
    data flowing through it.
    """
    if _depth > 8 or value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool | int | float):
        return str(value)
    if isinstance(value, dict):
        return " ".join(
            flatten_content(item, _depth + 1)
            for key, item in value.items()
            if not is_measurement_key(str(key))
        )
    if isinstance(value, list | tuple | set):
        return " ".join(flatten_content(item, _depth + 1) for item in value)
    return ""


def unsupported_items(claim_text: str, source_text: str, extractor: Any) -> set[str]:
    """Items ``extractor`` finds in the claim that are absent from the source.

    The unit of an unsupported-claim finding: a specific token the model
    asserted that nothing in its evidence contains.
    """
    return extractor(claim_text) - extractor(source_text)

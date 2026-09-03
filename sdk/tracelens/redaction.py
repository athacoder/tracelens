"""Payload capture policy and redaction (see section 31 of CLAUDE.md).

Trace payloads carry whatever flowed through the pipeline, which routinely
includes user data and sometimes credentials. Capture is therefore a policy
decision made at the SDK boundary, before anything is exported:

``full``      record payloads verbatim
``redacted``  record payloads with secret-looking keys and values masked (default)
``metadata``  record only a type/size summary, never the values

Redaction is deliberately conservative in both directions: it masks on key name
*or* value shape, and it never claims to be a complete DLP solution. It exists
so an obvious API key does not end up in the trace store by accident.
"""

from __future__ import annotations

import re
from typing import Any

from .models import CaptureMode

MASK = "[REDACTED]"

#: Key segments that make a value secret. Matched against whole segments, not
#: as substrings: an LLM span's ``prompt_tokens`` must survive while
#: ``access_token`` must not. Substring matching gets this backwards and
#: destroys the most common useful metadata in an AI trace.
SECRET_SEGMENTS = frozenset(
    {
        "apikey",
        "auth",
        "authorization",
        "cookie",
        "credential",
        "credentials",
        "passphrase",
        "passwd",
        "password",
        "pwd",
        "secret",
        "secrets",
        "ssn",
        "token",
    }
)

#: ``key`` on its own is too common to mask (a cache key, a dict key), so it
#: only counts as secret next to one of these.
KEY_QUALIFIERS = frozenset(
    {"access", "api", "encryption", "private", "public", "secret", "signing"}
)

_SEGMENT_SPLIT = re.compile(r"[^A-Za-z0-9]+")
_CAMEL_SPLIT = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

#: Value shapes that are masked regardless of the key they sit under.
SECRET_VALUE_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),  # OpenAI-style
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}"),  # Anthropic-style
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),  # GitHub tokens
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{16,}=*", re.IGNORECASE),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),  # JWT
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key id
)

MAX_DEPTH = 12


def segments(key: str) -> set[str]:
    """Split a key into lowercase words across ``_``, ``-``, and camelCase."""
    parts: list[str] = []
    for chunk in _SEGMENT_SPLIT.split(key):
        parts.extend(_CAMEL_SPLIT.split(chunk))
    return {p.lower() for p in parts if p}


def looks_secret(key: str) -> bool:
    """Whether a key's value should be masked based on its name alone."""
    found = segments(key)
    if found & SECRET_SEGMENTS:
        return True
    if "key" in found and found & KEY_QUALIFIERS:
        return True
    return "card" in found and bool(found & {"number", "no", "num"})


def scrub_text(text: str) -> str:
    """Mask secret-shaped substrings inside a string."""
    for pattern in SECRET_VALUE_PATTERNS:
        text = pattern.sub(MASK, text)
    return text


def summarize(value: Any) -> Any:
    """Describe a value without revealing it. Used by ``metadata`` mode."""
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return f"<str len={len(value)}>"
    if isinstance(value, dict):
        return {key: summarize(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return f"<{type(value).__name__} len={len(value)}>"
    return f"<{type(value).__name__}>"


def redact(value: Any, mode: CaptureMode = CaptureMode.REDACTED, _depth: int = 0) -> Any:
    """Apply the capture policy to an arbitrary payload.

    Structure is preserved in every mode so the forensic engine can still see
    which keys were present at each stage even when the values are hidden.
    """
    if mode is CaptureMode.FULL:
        return value
    if mode is CaptureMode.METADATA:
        return summarize(value)

    if _depth > MAX_DEPTH:
        return MASK
    if isinstance(value, dict):
        return {
            key: MASK if looks_secret(str(key)) else redact(item, mode, _depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item, mode, _depth + 1) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item, mode, _depth + 1) for item in value)
    if isinstance(value, str):
        return scrub_text(value)
    return value

"""Context propagation for the active trace and span stack.

Backed by ``contextvars``, so the active trace follows the logical flow of
control: it is inherited by tasks spawned with ``asyncio``, and it is isolated
per thread rather than shared globally. That matters because AI pipelines
routinely fan out retrieval or tool calls concurrently, and each branch has to
attach its spans to the right parent.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

from ..models import Span, Trace

_current_trace: ContextVar[Trace | None] = ContextVar("tracelens_trace", default=None)
_span_stack: ContextVar[tuple[Span, ...]] = ContextVar("tracelens_spans", default=())


def current_trace() -> Trace | None:
    """The trace this code is running inside, if any."""
    return _current_trace.get()


def current_span() -> Span | None:
    """The innermost open span, if any."""
    stack = _span_stack.get()
    return stack[-1] if stack else None


def span_stack() -> tuple[Span, ...]:
    return _span_stack.get()


def push_trace(trace: Trace) -> Token:
    return _current_trace.set(trace)


def pop_trace(token: Token) -> None:
    _current_trace.reset(token)


def push_span(span: Span) -> Token:
    return _span_stack.set((*_span_stack.get(), span))


def pop_span(token: Token) -> None:
    _span_stack.reset(token)


def set_span_stack(stack: tuple[Span, ...]) -> None:
    """Replace the stack outright.

    Needed because a span can be closed from a different context than the one
    that opened it, so resetting a token is not always available.
    """
    _span_stack.set(stack)


def clear() -> None:
    """Drop all context. For tests and for recovering from a torn-down run."""
    _current_trace.set(None)
    _span_stack.set(())

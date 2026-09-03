"""The instrumentation API.

Two design commitments shape this module.

First, one entry point. ``trace(name)`` starts a trace when nothing is active
and a child span when something is, so the same call reads correctly at the top
of a pipeline and inside a nested step. The user does not have to track which
they are in.

Second, instrumentation must never be the thing that breaks the pipeline. An
exporter that is down, a payload that will not serialise, a span closed twice:
none of these propagate to the caller unless ``strict`` is set. A tracing
library that takes production down has failed at its only job.
"""

from __future__ import annotations

import inspect
import logging
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from functools import wraps
from typing import Any, TypeVar

from ..exporters.base import Exporter
from ..exporters.memory import MemoryExporter
from ..models import (
    CaptureMode,
    ErrorInfo,
    Event,
    Span,
    SpanStatus,
    Stage,
    Trace,
    utcnow,
)
from ..redaction import redact
from . import context

logger = logging.getLogger("tracelens")

T = TypeVar("T")

MAX_STACKTRACE_CHARS = 8000


class Tracer:
    """Creates traces and spans and hands finished traces to an exporter."""

    def __init__(
        self,
        project: str = "default",
        pipeline: str = "default",
        exporter: Exporter | None = None,
        capture: CaptureMode = CaptureMode.REDACTED,
        strict: bool = False,
    ) -> None:
        self.project = project
        self.pipeline = pipeline
        self.exporter: Exporter = exporter if exporter is not None else MemoryExporter()
        self.capture = capture
        #: When true, instrumentation errors are raised instead of logged.
        #: Useful in tests, dangerous in production.
        self.strict = strict

    # -- primitives ------------------------------------------------------

    def _new_trace(self, name: str, attributes: dict[str, Any]) -> Trace:
        return Trace(
            name=name,
            project=self.project,
            pipeline=self.pipeline,
            attributes=dict(attributes),
        )

    def start_trace(self, name: str, **attributes: Any) -> Trace:
        """Begin a trace and make it current.

        The primitive form. ``end_trace`` clears the context again; prefer the
        ``trace()`` context manager, which cannot leak on an early return.
        """
        trace = self._new_trace(name, attributes)
        context.push_trace(trace)
        return trace

    def end_trace(
        self,
        trace: Trace | None = None,
        status: SpanStatus | None = None,
        end_time: datetime | None = None,
    ) -> Trace | None:
        """Close a trace, derive its status if not given, and export it."""
        trace = trace or context.current_trace()
        if trace is None:
            self._complain("end_trace called with no active trace")
            return None
        trace.end_time = end_time or utcnow()
        trace.status = status or (SpanStatus.ERROR if trace.failed else SpanStatus.OK)
        self.export(trace)
        if context.current_trace() is trace:
            context.clear()
        return trace

    def start_span(
        self,
        name: str,
        stage: Stage = Stage.OTHER,
        parent: Span | None = None,
        inputs: dict[str, Any] | None = None,
        trace: Trace | None = None,
        **attributes: Any,
    ) -> Span | None:
        """Begin a span inside the current trace and make it the active span."""
        trace = trace or context.current_trace()
        if trace is None:
            self._complain(f"start_span({name!r}) called with no active trace")
            return None

        parent = parent or context.current_span()
        span = Span(
            trace_id=trace.trace_id,
            parent_span_id=parent.span_id if parent else None,
            name=name,
            stage=stage,
            inputs=self._capture(inputs or {}),
            attributes=dict(attributes),
        )
        trace.add_span(span)
        context.push_span(span)
        return span

    def end_span(
        self,
        span: Span | None = None,
        status: SpanStatus | None = None,
        outputs: dict[str, Any] | None = None,
        end_time: datetime | None = None,
    ) -> Span | None:
        """Close a span. Status defaults to ok, or error if one is attached."""
        span = span or context.current_span()
        if span is None:
            self._complain("end_span called with no active span")
            return None
        if outputs is not None:
            span.outputs = self._capture(outputs)
        span.end_time = end_time or utcnow()
        span.status = status or (SpanStatus.ERROR if span.error else SpanStatus.OK)
        return span

    def record_event(self, name: str, span: Span | None = None, **attributes: Any) -> Event | None:
        """Attach a timestamped occurrence to a span."""
        span = span or context.current_span()
        if span is None:
            self._complain(f"record_event({name!r}) called with no active span")
            return None
        return span.record_event(name, **self._capture(attributes))

    def set_span_status(self, status: SpanStatus, span: Span | None = None) -> Span | None:
        span = span or context.current_span()
        if span is None:
            self._complain("set_span_status called with no active span")
            return None
        span.status = status
        return span

    def attach_error(self, error: BaseException, span: Span | None = None) -> Span | None:
        """Record an exception on a span and mark it failed."""
        span = span or context.current_span()
        if span is None:
            self._complain("attach_error called with no active span")
            return None
        span.error = ErrorInfo(
            type=type(error).__name__,
            message=str(error),
            stacktrace="".join(traceback.format_exception(type(error), error, error.__traceback__))[
                :MAX_STACKTRACE_CHARS
            ],
        )
        span.status = SpanStatus.ERROR
        return span

    def set_inputs(self, span: Span | None = None, **inputs: Any) -> None:
        span = span or context.current_span()
        if span is not None:
            span.inputs.update(self._capture(inputs))

    def set_outputs(self, span: Span | None = None, **outputs: Any) -> None:
        span = span or context.current_span()
        if span is not None:
            span.outputs.update(self._capture(outputs))

    def set_attributes(self, span: Span | None = None, **attributes: Any) -> None:
        span = span or context.current_span()
        if span is not None:
            span.attributes.update(self._capture(attributes))

    # -- scopes ----------------------------------------------------------

    @contextmanager
    def trace(self, name: str, **attributes: Any) -> Iterator[Trace]:
        """Run a block as a whole trace. Always closes, exports, and restores.

        The surrounding context is saved and restored rather than cleared, so
        a trace opened inside another trace does not strand the outer one.
        """
        saved_stack = context.span_stack()
        trace = self._new_trace(name, attributes)
        token = context.push_trace(trace)
        context.set_span_stack(())
        try:
            yield trace
        except BaseException as error:
            trace.attributes.setdefault("error", f"{type(error).__name__}: {error}")
            self.end_trace(trace, status=SpanStatus.ERROR)
            raise
        else:
            self.end_trace(trace)
        finally:
            context.pop_trace(token)
            context.set_span_stack(saved_stack)

    @contextmanager
    def span(
        self,
        name: str,
        stage: Stage = Stage.OTHER,
        inputs: dict[str, Any] | None = None,
        **attributes: Any,
    ) -> Iterator[Span | None]:
        """Run a block as one span. Exceptions are recorded, then re-raised."""
        span = self.start_span(name, stage=stage, inputs=inputs, **attributes)
        if span is None:
            yield None
            return
        try:
            yield span
        except BaseException as error:
            self.attach_error(error, span)
            self.end_span(span, status=SpanStatus.ERROR)
            raise
        else:
            self.end_span(span)
        finally:
            self._pop(span)

    def run(
        self,
        name: str,
        fn: Callable[..., T],
        *args: Any,
        stage: Stage = Stage.OTHER,
        **kwargs: Any,
    ) -> T:
        """Call ``fn`` inside a span, capturing its arguments and result.

        This is the form from the spec:
        ``docs = tracer.run("retriever", retrieve, query)``.
        """
        with self.span(name, stage=stage, inputs=_call_inputs(fn, args, kwargs)) as span:
            result = fn(*args, **kwargs)
            if span is not None:
                span.outputs = self._capture({"result": result})
            return result

    async def arun(
        self,
        name: str,
        fn: Callable[..., Any],
        *args: Any,
        stage: Stage = Stage.OTHER,
        **kwargs: Any,
    ) -> Any:
        """Async counterpart of :meth:`run`."""
        with self.span(name, stage=stage, inputs=_call_inputs(fn, args, kwargs)) as span:
            result = await fn(*args, **kwargs)
            if span is not None:
                span.outputs = self._capture({"result": result})
            return result

    def traced(
        self,
        name: str | None = None,
        stage: Stage = Stage.OTHER,
        **attributes: Any,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator form. Wraps sync and async functions alike."""

        def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
            span_name = name or fn.__qualname__

            if inspect.iscoroutinefunction(fn):

                @wraps(fn)
                async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                    with self.span(
                        span_name,
                        stage=stage,
                        inputs=_call_inputs(fn, args, kwargs),
                        **attributes,
                    ) as span:
                        result = await fn(*args, **kwargs)
                        if span is not None:
                            span.outputs = self._capture({"result": result})
                        return result

                return async_wrapper

            @wraps(fn)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                with self.span(
                    span_name,
                    stage=stage,
                    inputs=_call_inputs(fn, args, kwargs),
                    **attributes,
                ) as span:
                    result = fn(*args, **kwargs)
                    if span is not None:
                        span.outputs = self._capture({"result": result})
                    return result

            return wrapper

        return decorate

    # -- export ----------------------------------------------------------

    def export(self, trace: Trace) -> None:
        """Hand a finished trace to the exporter, swallowing exporter faults."""
        try:
            self.exporter.export(trace)
        except Exception:
            if self.strict:
                raise
            logger.warning("tracelens: export failed for trace %s", trace.trace_id, exc_info=True)

    # -- internals -------------------------------------------------------

    def _capture(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return redact(payload, self.capture)
        except Exception:
            if self.strict:
                raise
            logger.warning("tracelens: could not capture payload", exc_info=True)
            return {}

    def _pop(self, span: Span) -> None:
        """Remove ``span`` from the active stack.

        Rebuilds the stack rather than resetting a token, because a span can be
        closed from a different context than the one that opened it (a task
        that outlived its parent, or a manual end_span).
        """
        stack = context.span_stack()
        if stack and stack[-1] is span:
            context.set_span_stack(stack[:-1])
        else:
            context.set_span_stack(tuple(s for s in stack if s is not span))

    def _complain(self, message: str) -> None:
        if self.strict:
            raise RuntimeError(message)
        logger.warning("tracelens: %s", message)
        return None


def _call_inputs(fn: Callable[..., Any], args: tuple, kwargs: dict) -> dict[str, Any]:
    """Bind a call's arguments to their parameter names.

    Named arguments make the trace far more useful for invariant checking than
    a positional tuple would, and binding can fail on exotic signatures, so it
    degrades to positional capture rather than raising.
    """
    try:
        bound = inspect.signature(fn).bind_partial(*args, **kwargs)
        bound.apply_defaults()
        return {k: v for k, v in bound.arguments.items() if k not in ("self", "cls")}
    except (TypeError, ValueError):
        return {"args": list(args), **kwargs}


class Scope:
    """A trace-or-span scope that is both a context manager and a decorator.

    ``trace("customer-support")`` at the top of a pipeline opens a trace;
    the same call inside a running trace opens a child span. Making the
    distinction automatic is what lets one name serve both forms from the
    spec without the caller tracking nesting depth.
    """

    def __init__(
        self,
        tracer: Tracer,
        name: str | None,
        stage: Stage = Stage.OTHER,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        self._tracer = tracer
        self._name = name
        self._stage = stage
        self._attributes = attributes or {}
        self._active: Any = None

    def __enter__(self) -> Any:
        name = self._name or "trace"
        if context.current_trace() is None:
            self._active = self._tracer.trace(name, **self._attributes)
        else:
            self._active = self._tracer.span(name, stage=self._stage, **self._attributes)
        return self._active.__enter__()

    def __exit__(self, *exc_info: Any) -> Any:
        active, self._active = self._active, None
        return active.__exit__(*exc_info)

    def __call__(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        """Decorator form. A fresh scope is opened per call, not per decoration."""
        name = self._name or fn.__qualname__
        tracer, stage, attributes = self._tracer, self._stage, self._attributes

        if inspect.iscoroutinefunction(fn):

            @wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                with Scope(tracer, name, stage, attributes) as target:
                    tracer.set_inputs(**_call_inputs(fn, args, kwargs))
                    result = await fn(*args, **kwargs)
                    _record_result(tracer, target, result)
                    return result

            return async_wrapper

        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with Scope(tracer, name, stage, attributes) as target:
                tracer.set_inputs(**_call_inputs(fn, args, kwargs))
                result = fn(*args, **kwargs)
                _record_result(tracer, target, result)
                return result

        return wrapper


def _record_result(tracer: Tracer, target: Any, result: Any) -> None:
    """Store a call's return value on the span, when the scope opened one."""
    if isinstance(target, Span):
        tracer.set_outputs(span=target, result=result)

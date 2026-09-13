"""Regression: _inject_runtime must inject into a REQUIRED runtime param.

A forced tool call (fn-form / dispatch_forced_tool_call) invokes an
@agentic_function with only its declared input kwargs — never `runtime`.
The function's signature usually makes `runtime` a required positional
(e.g. `def f(pdf_path, runtime, …)`). _inject_runtime used a full
`sig.bind`, which raises "missing a required argument: 'runtime'" before
it can inject — breaking every real agentic function run via fn-form
(gui_agent / research_agent / extract_pdf_*). It must use bind_partial so
the injection branch can fill the gap, while still letting the caller's
own bind enforce that other required args were supplied.
"""
import inspect

import pytest

from openprogram.agentic_programming.function import (
    _inject_runtime,
    _current_runtime,
)
from openprogram.agentic_programming.runtime import Runtime


class _RT(Runtime):
    def __init__(self):
        self._question_transport = None

    def _ui_session_id(self):  # pragma: no cover - trivial
        return "s"


@pytest.fixture()
def ctx_runtime():
    rt = _RT()
    token = _current_runtime.set(rt)
    try:
        yield rt
    finally:
        _current_runtime.reset(token)


def _injected_runtime(args, kwargs):
    for v in list(args) + list(kwargs.values()):
        if isinstance(v, Runtime):
            return v
    return None


def test_required_runtime_param_gets_injected(ctx_runtime):
    """`def f(runtime, label='x')` — runtime has no default → required."""
    def f(runtime, label="x"):
        return runtime, label
    sig = inspect.signature(f)
    a, k, _tok, _own = _inject_runtime(sig, (), {"label": "t"})
    assert _injected_runtime(a, k) is ctx_runtime


def test_required_runtime_after_required_arg(ctx_runtime):
    """`def f(pdf_path, runtime, …)` — the extract_pdf shape."""
    def f(pdf_path, runtime, pages=""):
        return pdf_path, runtime, pages
    sig = inspect.signature(f)
    a, k, _tok, _own = _inject_runtime(sig, (), {"pdf_path": "/x.pdf"})
    assert _injected_runtime(a, k) is ctx_runtime


def test_default_runtime_param_still_injected(ctx_runtime):
    """`def g(runtime=None, …)` — the already-working default-value shape."""
    def g(runtime=None, label="x"):
        return runtime, label
    sig = inspect.signature(g)
    a, k, _tok, _own = _inject_runtime(sig, (), {"label": "t"})
    assert _injected_runtime(a, k) is ctx_runtime


def test_other_required_args_still_enforced(ctx_runtime):
    """Injection must not paper over a genuinely missing required arg —
    the caller's own sig.bind (after injection) still raises for it."""
    def f(runtime, required_arg):
        return 1
    sig = inspect.signature(f)
    a, k, _tok, _own = _inject_runtime(sig, (), {})
    with pytest.raises(TypeError, match="required_arg"):
        sig.bind(*a, **k)


def test_multiple_runtime_params_all_injected(ctx_runtime):
    """research_agent's shape: `runtime` AND `review_runtime`. BOTH must be
    filled with the same runtime — the old code `break`ed after one, so
    (with _RUNTIME_PARAMS an unordered set) it intermittently left
    review_runtime or runtime as None, and research_agent raised
    'requires a runtime argument'."""
    def research_agent(task, runtime=None, review_runtime=None):
        return task, runtime, review_runtime
    sig = inspect.signature(research_agent)
    a, k, _tok, _own = _inject_runtime(sig, (), {"task": "x"})
    bound = sig.bind(*a, **k)
    bound.apply_defaults()
    assert isinstance(bound.arguments["runtime"], Runtime)
    assert isinstance(bound.arguments["review_runtime"], Runtime)
    # Same shared instance, not two separate runtimes.
    assert bound.arguments["runtime"] is bound.arguments["review_runtime"]

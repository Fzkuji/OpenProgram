# `openprogram/functions/`

> Function calling — registry, presets, policy.

## Overview

Every function the LLM can call is decorated with ``@function`` (see
``_runtime.py``). The decorator builds an ``AgentTool`` and registers
it into a single in-process registry; this module imports each
subpackage so the side-effect registrations fire at import time, then
exposes the resolution API (presets, allow/deny/source policy chain).

Design synthesizes three external frameworks under ``references/``:

  - Claude Code: the ``AgentTool`` shape, the
    ``execute(call_id, args, cancel, on_update) -> AgentToolResult``
    contract, and the search/read collapse semantics.
  - Hermes: ``TOOLSETS`` with ``{tools, includes}`` composition,
    ``_expand_preset`` recursive walk + first-occurrence dedupe.
  - OpenClaw: per-channel ``unsafe_in`` filtering, allow/deny chain
    layered on top of the toolset (``apply_tool_policy``).

Beyond the references we add a dynamic per-call result ceiling, an
LLM-controllable timeout, a bounded streaming on_update accumulator,
and ``can_use()`` pre-flight gates — all wired through ``@function``
kwargs. See ``_runtime.py`` for the decorator and the helpers it
relies on.

## Files in this directory

- **`_helpers.py`** — Small helpers shared by tool `execute` implementations
- **`_providers.py`** — Shared provider-registry scaffolding for tools with pluggable backends
- **`_registry.py`** — Explicit + auto-discovered registry of @agentic_function modules
- **`_runtime.py`** — @function decorator + runtime layer

## Sub-packages

- **`agentics/`** — @agentic_function bodies
- **`tools/`** — Leaf LLM-callable tools

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._

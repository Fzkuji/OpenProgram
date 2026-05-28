# `openprogram/agentic_programming/`

> openprogram.agentic_programming — core engine.

## Overview

Primitives:

    1. @agentic_function  — turn a Python function into one that can call an LLM
    2. Runtime            — base class for an LLM-call runtime
    3. decision.make      — let the LLM make the next-step decision

Execution traces are persisted as a flat DAG in
``openprogram.context.storage`` (SQLite). Older revisions kept a
parallel in-memory ``Context`` tree + a JSONL trace + an event pubsub
layer; those have all been retired in favour of the DAG.

Zero downstream dependencies: providers / programs / webui depend on
agentic_programming, never the other way around.

## Files in this directory

- **`decision.py`** — decision
- **`function.py`** — agentic_function
- **`runtime.py`** — runtime
- **`session.py`** — Session management
- **`skills.py`** — Skill discovery and prompt formatting

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `_gen_dir_readmes.py` to refresh._

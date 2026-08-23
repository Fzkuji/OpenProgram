# `openprogram/agentic_programming/`

> openprogram.agentic_programming — core engine.

## Overview

Primitives:

    1. @agentic_function  — turn a Python function into one that can call an LLM
    2. llm                 — make one model request through the ambient Runtime
    3. agent               — make a tool loop through the ambient Runtime
    4. decision.make       — let the LLM make the next-step decision

Infrastructure:

    Runtime                — base class for provider calls and accounting

Execution traces are persisted as a flat DAG in
``openprogram.context.storage`` (SQLite). Older revisions kept a
parallel in-memory ``Context`` tree + a JSONL trace + an event pubsub
layer; those have all been retired in favour of the DAG.

Zero downstream dependencies: providers / programs / webui depend on
agentic_programming, never the other way around.

## Files in this directory

- **`agent.py`** — Agent: tool loop = repeatedly call llm + execute tools until done
- **`decision.py`** — decision
- **`function.py`** — agentic_function
- **`llm.py`** — One model request using the ambient agentic-programming Runtime
- **`runtime.py`** — runtime
- **`session.py`** — Session management
- **`tool_format.py`** — Convert an ``@agentic_function`` spec into other frameworks' tool formats

## Sub-packages

- **`control_flow/`** — Control flow primitives for agentic workflows

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._

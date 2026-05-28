# `openprogram/agent/`

> openprogram.agent — Agent algorithms (originally ported from pi-agent and

## Overview

the algorithmic core of pi-coding-agent).

Organized by concern:

* ``types``        — Agent event/state/tool type definitions
* ``agent_loop``   — Stateless agent loop function
* ``agent``        — Stateful ``Agent`` wrapping ``agent_loop``
* ``session``      — Lightweight ``AgentSession`` with auto-retry
* ``retry``        — Standalone retry-classification and backoff helpers
* ``messages``     — Custom message types (branch/compaction summaries, etc.)
* ``event_bus``    — Async pub/sub for agent events
* ``exec``         — Subprocess execution utility with timeout/cancellation
* ``compaction/``  — Token estimation, cut-point detection, LLM summarization

The Runtime layer composes these to build whatever agent behavior is needed.

## Files in this directory

- **`_approval.py`** — Tool-approval gate
- **`_event_parsing.py`** — Agent-event → chat envelope translation + usage extraction
- **`_merge.py`** — Merge N peer sessions into one target reply
- **`_model_tools.py`** — Agent-profile → Model + tools + history resolution
- **`_revert.py`** — Per-turn revert
- **`_turn_lifecycle.py`** — Assistant-turn lifecycle helpers
- **`_workdir.py`** — Default chat-runtime workdir resolution
- **`agent.py`** — Agent class
- **`agent_loop.py`** — Agent loop
- **`dispatcher.py`** — Single entry point for every conversation turn
- **`event_bus.py`** — Async event bus with channel-based pub/sub
- **`exec.py`** — Shared subprocess execution utilities
- **`messages.py`** — Custom message types and LLM converters for the agent layer
- **`plan_mode.py`** — Plan-mode session flag
- **`process_runner.py`** — Run @agentic_function tools in an isolated subprocess so the stop
- **`retry.py`** — Retry logic for agent errors
- **`session.py`** — AgentSession
- **`session_config.py`** — Per-session run configuration shared by TUI, web, and channels
- **`session_db.py`** — session_db
- **`sub_agent_run.py`** — Run an agent turn that can be inherited (sibling branch) or clean
- **`types.py`** — Agent types

## Sub-packages

- **`compaction/`** — Context compaction for long agent sessions
- **`streaming/`** — Streaming / resumable in-progress state
- **`task/`** — Async task lifecycle

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._

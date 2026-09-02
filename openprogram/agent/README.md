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
* ``exec``         — Subprocess execution utility with timeout/cancellation
* ``compaction/``  — Token estimation, cut-point detection, LLM summarization

The Runtime layer composes these to build whatever agent behavior is needed.

## Files in this directory

- **`_rewind.py`** — Plan and apply transactional multi-turn rewind operations
- **`agent.py`** — Agent class
- **`agent_loop.py`** — Agent loop
- **`attended.py`** — Attended / unattended mode
- **`authority.py`** — Runtime-owned speaker attribution and two-tier authorization
- **`continuation.py`** — Durable Agent checkpoint payloads and resumable loop input
- **`exec.py`** — Shared subprocess execution utilities
- **`history_ownership.py`** — Resolve owned child change sets for file-history operations
- **`inbox.py`** — Per-session send_message inbox
- **`messages.py`** — Custom message types and LLM converters for the agent layer
- **`plan_mode.py`** — Plan-mode session flag
- **`process_runner.py`** — Run @agentic_function tools in an isolated subprocess so the stop
- **`production_driver.py`** — Internal production driver for canonical Agent executions
- **`questions.py`** — User-input requests
- **`resource_governance.py`** — Resource-limit parsing, inheritance, and read-only job diagnostics
- **`retry.py`** — Retry logic for agent errors
- **`run_control.py`** — Run control for turn execution: cancel / session binding /
- **`session.py`** — AgentSession
- **`session_config.py`** — Per-session run configuration shared by TUI, web, and channels
- **`session_db.py`** — session_db
- **`sub_agent_run.py`** — Run an agent turn that can be inherited (sibling branch) or clean
- **`surface_context.py`** — Turn-scoped awareness of a visible OpenProgram desktop surface
- **`turn_request_context.py`** — The TurnRequest in force for the current execution context
- **`types.py`** — Agent types
- **`workspace_alignment.py`** — Conversation-branch and workspace alignment state

## Sub-packages

- **`compaction/`** — Context compaction for long agent sessions
- **`dispatcher/`** — Single entry point for every conversation turn
- **`internals/`** — Agent package internals
- **`job/`** — Async job lifecycle
- **`management/`** — Multi-agent support

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._

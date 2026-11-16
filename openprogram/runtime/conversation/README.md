# `openprogram/runtime/conversation/`

> Conversations: chat sessions and the turns that run in them.

## Overview

A turn takes a user message, runs the Agent call core
(``openprogram.runtime.agent``) with the session's history and
settings, and writes the result back into the session. ``turn_runtime`` runs
one turn, ``chat`` is the ``ChatAgent`` entry, and ``production_driver``
drives a turn as a canonical execution.

Importing this package registers the conversation engine with the call core
(``engine_registration``), so ``Agent.run_turn`` and the core's lookups of
the running turn reach it, and claims the core's cancellation check
(``run_control``), so a stopped turn aborts at the next Agent method entry.

## Files in this directory

- **`bound_reply.py`** — Deliver a bound chat's assistant replies to its channel conversation
- **`chat.py`** — Conversation policy on the public Agent execution contract
- **`checkpoint.py`** — Resuming an Agent turn from its journal
- **`event_parsing.py`** — Agent-event → chat envelope translation + usage extraction
- **`memory_agent.py`** — Synchronous memory-writer adapter over OpenProgram's normal Agent
- **`memory_execution.py`** — Bind a turn's memory policy for the length of its execution
- **`model_tools.py`** — Agent-profile → Model + tools + history resolution
- **`open_work.py`** — Open work: what a conversation's agent still owes the user
- **`process_notices.py`** — Tell the agent when a command it left running in the background ends
- **`process_runner.py`** — Run a turn's Agent method tools in an isolated subprocess
- **`session_context.py`** — Unified per-turn session context
- **`side_work.py`** — Background work a finished turn starts beside itself
- **`surface_context.py`** — Turn-scoped awareness of a visible OpenProgram desktop surface
- **`topics.py`** — Sort a conversation's messages into topics, for Activity's time chart

## Sub-packages

- **`always_on/`** — Always-on: an Agent configuration that makes its conversations wake
- **`layer_registrations/`** — What the conversation package registers with the layers below it when
- **`peer_agents/`** — Turns agents run for each other: starting a sub-agent run, merging a
- **`production_driver/`** — Internal production driver for canonical Agent executions
- **`rewinds/`** — Going back in a conversation: rewind and its marks, undo and redo of a
- **`run_control/`** — Run control for turn execution: cancel / session binding /
- **`session_settings/`** — What a conversation or one of its branches is configured to run with:
- **`turn_admission/`** — How typed text or an owner event becomes an admitted turn: the turn
- **`turn_runtime/`** — Shared persistent-turn execution for Agent and ChatAgent

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/generators/gen_dir_readmes.py` from the repo root to refresh._

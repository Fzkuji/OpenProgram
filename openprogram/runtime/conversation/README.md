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
- **`branch_config.py`** — Bind execution settings to a branch before an input becomes durable
- **`branch_fork.py`** — Where a retry or an edit starts its new branch, decided without the server
- **`chat.py`** — Conversation policy on the public Agent execution contract
- **`checkpoint.py`** — Resuming an Agent turn from its journal
- **`context_registration.py`** — What the conversation package adds to the context the model sees
- **`engine_registration.py`** — The conversation engine's implementation of the call core's engine link
- **`event_parsing.py`** — Agent-event → chat envelope translation + usage extraction
- **`execution_hooks.py`** — The Agent engine's registrations with the execution control plane
- **`history_ownership.py`** — Resolve owned child change sets for file-history operations
- **`inbox.py`** — Per-session send_message inbox
- **`local_commands.py`** — Slash commands a surface answers itself, without a model turn
- **`memory_agent.py`** — Synchronous memory-writer adapter over OpenProgram's normal Agent
- **`memory_execution.py`** — Bind a turn's memory policy for the length of its execution
- **`merge.py`** — Merge N peer sessions into one target reply
- **`model_tools.py`** — Agent-profile → Model + tools + history resolution
- **`open_work.py`** — Open work: what a conversation's agent still owes the user
- **`owner_turn.py`** — Start an owner chat turn in a conversation without a client sending it
- **`permission_registration.py`** — The session and turn facts the permission package reads
- **`plan_mode.py`** — Plan-mode session flag
- **`process_notices.py`** — Tell the agent when a command it left running in the background ends
- **`process_runner.py`** — Run a turn's Agent method tools in an isolated subprocess
- **`restart_turn.py`** — Continue an interrupted chat as a new turn after a worker restart
- **`revert.py`** — Safe single-turn Undo/Revert and Redo/Reapply
- **`rewind.py`** — Plan and apply rewinds in three modes: code and conversation, conversation, code
- **`rewind_marks.py`** — What a rewind leaves behind: Undo and Redo marks and the note to the model
- **`run_note.py`** — What an unattended turn is told about the run it is in
- **`session_config.py`** — Per-session run configuration shared by TUI, web, and channels
- **`session_context.py`** — Unified per-turn session context
- **`session_model.py`** — Single source of truth for which chat model a session runs
- **`side_work.py`** — Background work a finished turn starts beside itself
- **`sub_agent_run.py`** — Run an agent turn that can be inherited (sibling branch) or clean
- **`surface_context.py`** — Turn-scoped awareness of a visible OpenProgram desktop surface
- **`topics.py`** — Sort a conversation's messages into topics, for Activity's time chart
- **`turn_input.py`** — A chat message someone typed, as the turn request it becomes
- **`turn_lifecycle.py`** — Assistant-turn lifecycle helpers
- **`turn_request_context.py`** — The TurnRequest in force, plus the owner's original input
- **`workdir.py`** — Default chat-runtime workdir resolution

## Sub-packages

- **`always_on/`** — Always-on: an Agent configuration that makes its conversations wake
- **`production_driver/`** — Internal production driver for canonical Agent executions
- **`run_control/`** — Run control for turn execution: cancel / session binding /
- **`turn_runtime/`** — Shared persistent-turn execution for Agent and ChatAgent

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._

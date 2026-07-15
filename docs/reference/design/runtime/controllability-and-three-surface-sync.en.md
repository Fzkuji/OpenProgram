# Controllability of long-running agent tasks + three-surface consistency

User requirements (paraphrased from the original):
1. **Attended/unattended switch**: when unattended (I'm asleep), don't ask me; when watching, you may ask. The approach is settled — when unattended, simply **don't give the agent the "ask the user" tool**; it can't ask if it can't reach it, and at worst it produces some uncertain content that later model reasoning resolves on its own.
2. **Mid-run intervention / redirection**: if I notice halfway through the task that the direction is wrong, I can inject a new instruction to make it adjust. The direction is settled — **built on the event layer**, the program continuously monitors external events, and any extra user message gets injected.
3. **Mid-run question display**: when the agent wants to ask me, all three surfaces can see it and answer.
4. **Three-surface session sync**: a session running in the background on one surface displays in sync across CLI/TUI/web; one surface acting must not leave another surface unaware.

## Foundation inventory (existing, reusable)

| Capability | Current state | file:line |
|---|---|---|
| steering injection points | agent_loop already has 3 checkpoints (turn head, after each tool, before follow-up) where messages can be inserted | `agent/agent_loop.py:218-310,620-629` |
| question framework | QuestionRegistry (process-level, thread-safe, claim-once) + two Transports (EventLayer / Queue) + subprocess bridge | `agent/questions.py:34-198`, the answer_queue/QueueTransport in `agent/process_runner.py` |
| event broadcast | EventBus + emit_ws_frame: emit in one place, all WS clients receive it | `agent/event_bus.py:115-125`, `_broadcast` in `webui/server.py` |
| shared storage | git-backed SessionDB, one source of truth shared by all three surfaces; worker singleton (WorkerLock) | `agent/session_db.py`, `worker/lock.py` |
| tool catalog policy | apply_tool_policy supports deny/allow; toolset tiering (default excludes ask_user_question, full includes it) | `functions/__init__.py` apply_tool_policy |
| cancel / graceful stop | cancel_event end-to-end + subprocess graceful stop IPC (added this round) | `agent/process_runner.py` request_graceful_stop |

**Key conclusion**: all three surfaces share worker + DB + EventBus, so the foundation for sync is already in place; the pipes for steering and asking questions are both ready-made. The real vacuum is the "gate" and the "coordination layer".

## Important finding: where the attended switch actually lands

The autonomous loop in research_harness is **already unattended in practice**:
- `oversight="interactive"` already excludes socratic_plan and the like from the catalog (`registry.py:357`).
- The experiment sub-functions use `toolset="default"`, and default **does not include** `ask_user_question`.

So the rule "don't give the ask tool when unattended" **already holds naturally** on the research_harness autonomous path. Where the attended switch actually matters is the **OpenProgram general chat/agent path** (there the `full` toolset includes `ask_user_question` + the clarify tools).

→ P1 landing point = when dispatcher selects tools for a turn, decide whether to deny `ask_user_question` / clarify-type tools based on the session-level `attended` flag.

## Phased plan (each step independently verifiable, independently shippable)

### P1 — attended switch (attended/unattended)
- **Session-level flag** `attended` (default attended=True, i.e. asking is allowed). Where it lives: session metadata (SessionDB) + in-memory state, readable and writable by all three surfaces.
- **Gate**: when dispatcher assembles a turn's tools, `attended=False` → `apply_tool_policy(deny=["ask_user_question","clarify",...inquiry-type])`. The agent has no such tool = it won't ask.
- **Set the switch from all three surfaces**: a CLI flag (`--unattended`), a TUI shortcut/status, a web toggle. All three change the same session flag (via the worker).
- **Verification**: under unattended, run a task that would normally ask, and confirm the catalog has no ask tool, the agent pops no questions, and it still produces output.

### P2 — mid-run intervention (event-injected steering)
- **Mechanism**: turn a "user mid-run message" into an event; research_agent / agent_loop poll a **session-level steering queue** at the loop checkpoints (the same two points as max_runtime_s/stop_event), and if there is one, inject the message as a new instruction into the next step's context / re-run _pick_stage.
- **Entry point**: any surface sends an "intervention message" → WS/CLI → push it into that session's steering queue (reuse the server's `_follow_up_queues` or create a new one).
- **Injection semantics**: don't interrupt the current step (graceful); once the current step finishes, feed the user's new instruction into the next round's stage decision ("the user said mid-run: change to X, adjust accordingly").
- **Reuse**: agent_loop already has a steering_messages pipe; the research_agent loop adds a `_steering_pending()` check, alongside `_stop_requested()`.
- **Verification**: when the task reaches stage 2, send "stop writing experiments, do the literature review first", and confirm the next round's stage decision picks this up and pivots.

### P3 — completing three-surface sync
- **running status bit**: add a "computing now" flag to the session (who is running, how far along), written into broadcastable session state, so all three surfaces can see "this session is busy".
- **Replay on new connection**: when a new WS client joins, if the session has a turn that is currently streaming, resend the intermediate events that have already occurred (or at least send "running now + current stage"). Right now a new connection can only see the final.
- **Verification**: surface A starts a task, surface B connects midway, and B can see "running now + progress" rather than blank-waiting for the final.

### P4 — aligning question display
- CLI / TUI render the `question.asked` event as an answerable card (web already has it). CLI uses a stdin prompt, TUI uses the follow_up card component.
- **Verification**: under attended, the agent asks a question, all three surfaces can pop it and answer it, and the answer goes back to the same registry.

## Cross-cutting: three-surface consistency is a pervasive principle
P1's switch, P2's intervention, P4's questions — their state all lives at the **session level** (inside the worker + DB), and the three surfaces are just different views of this same state. An action on any surface goes through the worker → broadcast → all three surfaces sync. Never create state that is local to one surface and unsynced.

## Design points awaiting the user's decision
- P1 default: attended=True (asking allowed by default) or unattended=True (don't disturb by default)?
- P2 injection timing: only graceful injection at step boundaries (recommended), or also support "interrupt the current step immediately"?
- Order: whether to follow exactly P1→P2→P3→P4.

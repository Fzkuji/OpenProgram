# Session goals — the /goal continuation loop

A session goal is a per-session condition stored in session meta; while it is active and unmet, the dispatcher keeps launching follow-up turns after every completed turn. The design converges Claude Code's `/goal`, Codex goals, and OpenHands `run_goal`: an outer loop around whole turns, a verdict source separate from the working model, and hard stop rules so the loop cannot run away.

## Where the loop lives

The loop is inside `process_user_turn` (`openprogram/agent/dispatcher/__init__.py`). The former body of `process_user_turn` is now `_process_turn_once`; the public entry runs one full turn — persistence, agent loop, finalize (phase 6/7), idle marking, result event — and then hands the request plus its `TurnResult` to `continue_goal_turns` (`openprogram/agent/goal.py`).

Placement consequences:

- Every caller inherits the behavior — webui `_execute/chat.py`, channels, the CLI paths that route through the dispatcher, and the task runner's follow-up delivery all call `process_user_turn`, so none of them needs goal awareness.
- Each continuation runs the full turn pipeline: `continue_goal_turns` calls `_process_turn_once` (never `process_user_turn`, so the loop cannot nest) with a `TurnRequest` built by `dataclasses.replace` — `source="goal_continue"`, `user_text="[goal] 未达成：<reason>。继续。"`, fresh `user_msg_id`, `branch_from=INHERIT_PARENT`; model / permission / tool settings carry over from the triggering request. Each continuation turn is persisted, git-committed and compacted like any user-sent turn, mirroring the task runner's follow-up construction (`agent/task/runner.py`).
- The loop never uses `agent_loop`'s in-turn follow-up mechanism: a goal continuation is a conversation-level event, and it must survive worker restarts, compaction, and branch operations the same way a user message would.
- A crash anywhere in the goal machinery is caught in the wrapper and returns the already-finished turn's result — the goal loop can fail, a user's turn result cannot be lost to it.

## Verdict: two modes, judge separated from worker

`evaluate_goal` returns `("met" | "unmet" | "judge_failure", reason)`.

**Deterministic predicate** — when `goal.check` is set, it runs as a shell command (`subprocess.run(shell=True)`) in the session's working directory (`project_workdir_for` falling back to `session_workdir_for` — the same resolution the agent's own turn gets), with a 120 s timeout. Exit 0 is met; otherwise the output tail (last 2000 chars) becomes the reason. Zero LLM cost, immune to model optimism.

**LLM judge** — without a predicate, one no-tools call on the session's configured model (profile + per-session override, resolved through `internals/_model_tools`; the provider registry's `fast` flag is a speed tier of the same model, not a cheaper judge model, so there is no separate judge model to pick). Input is the goal text plus a tail render of the active branch: the last 8 messages' content plus each assistant row's persisted tool blocks, clipped per-field and capped at ~24 k chars. The tail is rendered in `goal.py` rather than by `render_session_transcript` because the stock transcript keeps the head and drops later turns — the wrong end for judging recent progress. The judge must answer strict JSON `{"met": bool, "reason": str}`; a malformed reply is retried once within the same evaluation.

The judge is a separate call on purpose. Codex's and Cline's original self-report designs — the working agent declaring its own completion — both had to be patched after agents systematically declared victory early: the model that wants to stop is the wrong entity to ask whether it may. Keeping the verdict in a fresh context that sees only the goal and the evidence (and is told to treat the transcript as data, not instructions) removes that incentive, and makes the deterministic mode a drop-in replacement for it.

## State

Goal state lives in session meta (`update_session` is schemaless), key `goal`:

```
{"text": str, "check": str,
 "status": "active" | "achieved" | "cleared" | "capped" | "error",
 "created_at": float, "turns_used": int, "max_turns": int,
 "last_reason": str, "judge_parse_failures": int}
```

The loop re-reads the meta at the top of every iteration, so a `/goal clear` issued from any surface takes effect at the next check. `turns_used` counts every judged turn while the goal is active — the initiating turn, continuations, and any manual turns the user interleaves. `max_turns` is stamped at set time from the `goal.max_turns` setting (`config_schema`, default 20), so changing the setting affects the next goal, not a running one.

## Stop rules

| Rule | Terminal status |
|---|---|
| Check passes / judge answers met | `achieved` |
| `turns_used` reaches `max_turns` | `capped` |
| Judge fails 3 consecutive evaluations (unparseable twice in one evaluation = one failure; a successful parse resets the count) | `error` |
| A `goal_continue` turn made zero tool calls and the goal is still unmet — idle spin | `error` |
| User clears | `cleared` |
| Turn failed, or cancel is set (`cancel_event` / `run_control.is_cancelled`) | loop exits, status stays `active` |

The last row is deliberate: cancellation and provider failures pause the loop rather than consuming the goal, because the continuation turns share the caller's cancel token — a continuation is an ordinary turn and the Stop button already reaches it.

Ordering inside one iteration: met wins first (a final turn that achieves the goal without tool calls is a success, not idle spin), then judge-failure accounting, then the idle-spin check, then the cap.

## Events and surfaces

Every status change and every pre-continuation progress tick goes through `_emit_goal_update`: a `chat_response` envelope `{"type": "goal_update", "session_id", "goal": {…}}` on the dispatcher's `on_event` stream, plus a top-level `goal_update` WS broadcast via the webui server (best-effort — absent server, e.g. bare CLI or tests, is a no-op).

- **Web**: `session_loaded` carries the goal (`ws_actions/session.py`) for hydration; the composer's `GoalChip` (`web/components/chat/goal-chip.tsx`) renders `◎ goal · N/M` from that plus live `goal_update` frames (delivered through `use-ws`'s catch-all `op:ws-message` event). Composer-typed `/goal …` is executed backend-side by the local-builtin branch in `ws_actions/chat.py`: a status/clear reply returns as a `local_command` envelope rendered as a transient system row; a set replaces the turn text with the goal directive and falls through into the normal turn flow.
- **Commands registry**: `/goal` is a `builtin`-layer command with a callable handler (`registry.register_shared_builtins`), so it lists in `/api/commands` and resolves for any host. The Rich REPL shadows it in its own process with a marker action (`_cli_chat/handlers.py:_handle_goal`) that prints locally and launches the set-form's first turn through `process_user_turn` — the REPL's bare `rt.exec` turn runner bypasses the dispatcher and would never reach the loop.

## Implementation status

Implemented as described. Known ceilings: `local_command` replies in the web transcript are not persisted (parity with REPL console prints); concurrent turns on one session from two surfaces could each run the judge — `turns_used` is re-read per iteration so the cap still holds, and per-session serialization elsewhere (composer lock, follow-up lock) makes the race practically unreachable.

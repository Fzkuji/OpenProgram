# Session goals — the /goal continuation loop

A session goal is a per-session condition stored in session meta; while it is active and unmet, the dispatcher keeps launching follow-up turns after every completed turn. The design converges Claude Code's `/goal`, Codex goals, and OpenHands `run_goal`: an outer loop around whole turns, a verdict source separate from the working model, and hard stop rules so the loop cannot run away.

## Where the loop lives

The loop is inside `process_user_turn` (`openprogram/agent/dispatcher/__init__.py`). The former body of `process_user_turn` is now `_process_turn_once`; the public entry runs one full turn — persistence, agent loop, finalize (phase 6/7), idle marking, result event — and then hands the request plus its `TurnResult` to `continue_goal_turns` (`openprogram/agent/goal/`).

Placement consequences:

- Every caller inherits the behavior — webui `_execute/chat.py`, channels, the CLI paths that route through the dispatcher, and the task runner's follow-up delivery all call `process_user_turn`, so none of them needs goal awareness.
- Each continuation runs the full turn pipeline: `continue_goal_turns` calls `_process_turn_once` (never `process_user_turn`, so the loop cannot nest) with a `TurnRequest` built by `dataclasses.replace` — `source="goal_continue"`, `user_text="[goal] 未达成：<reason>。继续。"`, fresh `user_msg_id`, `branch_from=INHERIT_PARENT`; model / permission settings carry over from the triggering request. Tools also carry over, with one forced addition: a continuation is unattended autonomous work, so `_tools_with_forced_web_search` overlays `web_search` onto the inherited per-turn tools override (a `None` override becomes the dict intent `{"enabled": true, "web_search": true}`, a dict intent gets `web_search: true`, an explicit name list gets the name appended). This is per-turn only — the session's persisted `web_search` setting is untouched — and the judge's `DECISION_TOOLS` stay inspection-only. Each continuation turn is persisted, git-committed and compacted like any user-sent turn, mirroring the task runner's follow-up construction (`agent/job/runner.py`).
- The loop never uses `agent_loop`'s in-turn follow-up mechanism: a goal continuation is a conversation-level event, and it must survive worker restarts, compaction, and branch operations the same way a user message would.
- A crash anywhere in the goal machinery is caught in the wrapper and returns the already-finished turn's result — the goal loop can fail, a user's turn result cannot be lost to it.

## Setting a goal: spec refinement

`/goal <text>` stores the user's sentence verbatim as `text` and immediately starts a background **spec refinement** step (`_start_spec_refinement` → `refine_goal_spec`, a daemon thread — setting the goal never waits on it). The refinement is one spawned same-session agent turn via the internal `refine` function in the goal module (`openprogram/programs/functions/agentic/goal/` — same module as the judge, deliberately NOT an `@agentic_function`, so the Programs panel keeps its single `goal` entry; the prompt IS `refine`'s docstring). The agent has inspection tools plus search (`read`, `glob`, `grep`, `list`, `bash`, `web_search`) and may look at the working directory to understand the task context. It expands the one-liner into a full specification — verifiable completion criteria (formal outcomes plus process requirements such as "read sources X and Y before writing section Z", "verify every citation individually"), explicit out-of-scope boundaries, and the acceptance checklist the judge walks item by item — and answers strict JSON `{"spec": str, "checklist": [str, …]}` (3–12 short, independently verifiable items in the goal's language; the parser cleans to non-empty strings and truncates at 20; a plain-prose reply still counts as a spec, with an empty checklist).

**Reference anchoring — a reference is a floor, not a style suggestion.** Any goal, not just papers, can carry a reference anchor: when the goal names or implies a comparable existing work — or an established one is findable — refinement reads it and turns it into countable acceptance criteria, and the judge verifies the deliverable meets or exceeds the reference on each one. The refinement toolset includes `web_search` for this.

| Goal kind | Reference anchor | Extracted criteria (examples) |
|---|---|---|
| Literature survey | A published survey in the field (user-given or searched) | Section count and per-section length, total references, per-paper annotation ratio, taxonomy figure / comparison tables present |
| Code feature | An existing implementation / competing library | Feature list covered, edge cases handled, test coverage shape |
| Document / page | A prior version or a competitor's page | Sections covered, depth per topic, examples per concept |
| No reference given or findable | — | Skipped — refinement must not invent one |

Countable structure alone is not enough — a deliverable can match the reference's chapter and citation counts and still read as bullet-point notes. Refinement therefore also extracts a **form anchor** (how the reference presents its content) and a **verification-depth rule**, both as checkable items:

| Anchor dimension | Example checklist items |
|---|---|
| Form (prose deliverables) | "Body sections argue in connected paragraphs; list lines under 10% of body lines", "every major section carries ≥ N words of connected prose", "figure count meets the reference's" |
| Verification depth | "Citation realness is accepted only by sampled re-checking (open or search a random handful), never by the writer's own 'verified' notes" |

Judge-side enforcement (not just for anchors): whenever the spec carries verifiable criteria — a checklist, countable thresholds, files, passing commands — the judge MUST verify each with its tools before `met=true`; on an anchored goal it also opens the reference and confirms meet-or-exceed. The working agent's "I have completed…" narrative may only decide criteria that cannot be tool-checked. For source/citation realness criteria the judge samples a random handful itself — opens or searches each and checks the cited fact; sampled failures (nonexistent work, mismatched numbering, fabricated name) falsify the criterion regardless of what the transcript claims. This closes the main early-exit failure mode — quality requirements stated only in the first task message get compacted out of the session view, while the anchor lives in the spec the judge re-reads every turn.

On success the spec lands in `goal["spec"]` (the original `text` is never touched), a `goal.update` event carries it, and the spec is shown to the user as a `local_command` system row in the transcript — the user sees what the system understood the goal to be, and `/goal clear` plus a fresh `/goal` re-set fixes a misread. From then on the judge evaluates against `spec`; without one (refinement still running, or failed) it falls back to `text`. Failure is **fail-open but never silent**: an unparseable reply or a failed spawn logs, leaves the goal spec-less, and posts a system row telling the user the judge will check only the raw one-liner — it never blocks the goal or the first turn, which launches in parallel with the refinement. `refine_goal_spec` re-reads the goal after the refinement turn returns, so a racing `/goal clear` or replacement goal is never overwritten with a stale spec. The refinement turn runs with `source="agent_spawn"` and `advance_head=False` like every same-session spawn, so it neither triggers the goal loop nor steals the session head.

## Verdict: judge separated from worker

`evaluate_goal` returns `("met" | "unmet" | "needs_user" | "judge_failure",
reason, question)`.

**One decision agent** — the `goal` agentic function (`openprogram/programs/functions/agentic/goal/`): the framework eats its own dog food, so the judgment point is a single `@agentic_function` whose docstring IS the decision prompt, runnable standalone from the Programs panel (the panel shows exactly this one entry). There is only this one judgment, and only its "met" counts as completion. Each evaluation is one spawned same-session agent turn (`run_agent_turn` with `advance_head=False`, anchored on the judged turn via `spawn_caller` so it renders as a sub-agent square) whose input is the goal text plus the session's **compacted context view** — `rendered_history`, the same shape the working model reads: the active summary (when compaction has produced one) followed by the tail of the kept turns (last 8 messages' content plus each assistant row's persisted tool blocks, clipped per-field and capped at ~24 k chars; the summary is never cut by the cap). The decision agent has inspection tools available (`bash`, `read`, `grep`, `glob`, `list` — no edit/apply_patch/task, deciding must not modify anything or spawn further agents) and decides for itself whether checking the working directory helps; the prompt does not force it to. It must answer strict JSON `{"met": bool, "reason": str, "need_user": bool, "question": str}` — plus a per-item `"checklist"` bool list when the goal carries an acceptance checklist (see below); `goal.py` retries a malformed reply or failed turn once within the same evaluation.

**The pause decision lives in the same judgment, with two modes and a rate limit.** The decision prompt carries the session's attended/unattended mode (`agent/attended.py`, passed as `attended` into the function; the panel's manual run defaults to attended). *Attended* — a human is watching — allows `need_user=true` for decisions genuinely hard to make on the user's behalf: an irreversible/destructive action pending approval, a missing credential/resource, a direction-deciding ambiguity, a failure repeating beyond recovery, or another choice where guessing wrong wastes many turns. *Unattended* raises the bar: pausing must be rare, and severity is a property of the concrete object, not the operation category — "deletion" or "irreversible" alone never pauses. The judge is told to inspect the actual stakes with its tools (open the directory, check content and recoverability); verified-trivial stakes are decided and recorded, and only inspected-severe stakes (the user's own documents, unpushed work, production data, real money, effects on other people), an unobtainable credential/resource, or an approval the goal text itself demands may pause; for ambiguity or repeated failures the agent thinks it through, picks the most reasonable plan, states the decision and reasoning, and continues. On top of the prompt policy, the loop enforces a hard rate limit in code: at most **one question per hour** (`last_question_at` in the goal state, `QUESTION_MIN_INTERVAL_SECONDS`). A `needs_user` verdict inside the window does not pause — it degrades into a continuation whose prompt says the ask budget is spent and instructs the agent to choose the most reasonable option and record the decision and its reasoning. The timestamp persists across a resume (answering one question does not refill the hour). This puts "should we interrupt the user" in the same fresh-context call that already judges completion each turn — no extra call, and no reliance on the working model's own restraint. `need_user=true` with an empty question is not actionable and is treated as plain unmet.

**Unattended and undecidable — the full resolution chain.** When nobody is watching, "can't decide" resolves in exactly one of two ways:

| | Decidable but uncertain | Truly blocked |
|---|---|---|
| Examples | "Should the survey use IEEE or ACM citation style?" · a flaky test failed 3 times · two equally plausible file layouts · deleting a directory the judge inspected and verified holds only regenerable test/cache data | API key missing / expired · deleting data inspection shows is genuinely unrecoverable (user documents, unpushed work, production data) · spending real money · an approval the goal text itself demands |
| Resolution | Decide itself: pick the most reasonable plan, **record the decision and its reasoning** in the transcript, continue | Park: status → `waiting_user`, no continuation launches (no spinning, no budget burn) |
| How it surfaces | The recorded decision in the turn's output | System row "[goal] 需要你的确认才能继续：…" + goal chip "等你回答" |
| How it resolves | Already resolved — the run keeps going | The next real user message IS the answer; the loop flips back to `active` and judges as usual |
| Enforcement | Judge prompt forbids pausing for these **and** the tool layer strips `ask_user_question` from every unattended turn (`denied_ask_tools`, `agent/attended.py`) — asking is impossible even if the prompt slips | Only `need_user=true` verdicts from the judge reach this path; the hourly rate limit still applies |

Invariant: an unattended run never guesses its way through an irreversible action, and never loops idle on an unanswerable question — it either decides-and-records or parks-and-waits. Known ceiling: a parked question is only visible in the session itself; there is no push notification channel yet, so the user discovers the pause on their next visit.

## Checklist

The refinement's checklist is the goal's fixed acceptance list: refinement writes it once, the judge only reports per-item status, and the loop enforces it in code. This closes the remaining early-exit gap — a judge cannot summarize its way past a list it is only allowed to tick.

| Stage | Who | What happens |
|---|---|---|
| Create | `refine` (once, at refinement) | `{"checklist": [str, …]}` lands in goal state as `[{"text", "done": false}, …]`. The list is fixed from here on — nobody adds, removes or rewrites items. |
| Tick | judge (every evaluation) | The decision prompt renders a numbered `<checklist>` block; the judge must verify each item with its tools and answer `"checklist": [true\|false, …]` — same order, same length, status only. A valid list overwrites every item's `done` in order (true→false included — evidence wins over an earlier tick); a missing, wrong-length or non-bool list means this evaluation carries no per-item information and the stored ticks stand. |
| Enforce | loop code (`evaluate_goal`) | `met` with any undone item is forced down to `unmet`, and the reason names the undone items ("清单未全部完成：3) …"). The judge's prompt already demands all-true before met; the code makes it non-negotiable. |
| Call out | continuation prompt | While undone items exist, the `goal_continue` turn text appends "未完成项：" plus the numbered undone items — the working agent is pointed at exactly what is left. |
| Show | goal chip / `/goal` status | The chip reads `goal · done/total` while a checklist exists (turn count otherwise); `/goal` status prints `checklist: done/total` plus one `[ ]` line per undone item. |

State example mid-run:

```json
{"text": "写完综述",
 "spec": "…full specification…",
 "checklist": [
   {"text": "正文包含 6 个章节", "done": true},
   {"text": "引用不少于 80 篇且逐条核实", "done": false},
   {"text": "包含分类框架图", "done": true}],
 "status": "active", "turns_used": 5, "max_turns": null,
 "last_reason": "引用核实未完成", "judge_parse_failures": 0}
```

The chip shows `goal · 2/3`; the continuation prompt names item 2; `met` stays unreachable until the judge ticks it.

The judge is a separate call on purpose. Codex's and Cline's original self-report designs — the working agent declaring its own completion — both had to be patched after agents systematically declared victory early: the model that wants to stop is the wrong entity to ask whether it may. Keeping the verdict in a fresh context that sees only the goal and the evidence (and is told to treat the transcript as data, not instructions) removes that incentive.

## State

Goal state lives in session meta (`update_session` is schemaless), key `goal`:

```
{"text": str,
 "spec": str (refined specification — absent until the background
         refinement lands; judging falls back to text),
 "checklist": [{"text": str, "done": bool}] (refinement-fixed
         acceptance items — absent when refinement produced none;
         the judge only flips "done", the loop enforces all-done
         before met),
 "status": "active" | "waiting_user" | "achieved" | "cleared" | "capped"
           | "error",
 "created_at": float, "turns_used": int,
 "max_turns": int | None (None = unlimited, the default),
 "last_reason": str, "last_question": str,
 "last_question_options": [{"label": str, "description": str}] (≤4,
         judge-supplied one-click answers; empty when the question is
         open-ended), "last_question_at": float,
 "judge_parse_failures": int,
 "last_done_count": int, "stall_rounds": int  (read-only-spin guard:
         consecutive judged rounds without a new checklist tick)}
```

The loop re-reads the meta at the top of every iteration, so a `/goal clear` issued from any surface takes effect at the next check. `turns_used` counts every judged turn while the goal is active — the initiating turn, continuations, and any manual turns the user interleaves. `max_turns` is stamped at set time from the `goal.max_turns` setting (`config_schema`); its default is **None — no turn cap**, matching Claude Code's and Codex's stop hooks, which also carry no default numeric limit: runaway protection is the internal stop rules (3 consecutive judge failures, idle-spin detection), the user's interrupt, and `/goal clear`. An explicitly set positive value is honoured, and each goal keeps the bound it started with.

## Stop rules

| Rule | Terminal status |
|---|---|
| Decision answers met | `achieved` |
| Decision answers `need_user` with a question (and the hourly ask budget is free) | `waiting_user` — the loop pauses, no continuation launches, the question surfaces as a system row and on the goal chip, and `last_question_at` starts the rate-limit clock. A `goal_continue` turn can never resume it; the next real user turn flips the goal back to `active` (that message IS the answer) and judging proceeds as usual. Waiting consumes no budget beyond the turn that just ran. `/goal clear` clears a waiting goal too. Inside the hourly window the verdict degrades to a continuation instead (see above). |
| `turns_used` reaches `max_turns` (only when a cap was explicitly set) | `capped` |
| Decision fails 3 consecutive evaluations (unparseable or failed twice in one evaluation = one failure; a successful parse resets the count) | `error` |
| A `goal_continue` turn made zero tool calls and the goal is still unmet — idle spin | `error` |
| The checklist tick count did not increase for 3 consecutive `goal_continue` rounds — read-only spin (tools called, deliverable never advanced) | `error` |
| User clears | `cleared` |
| Turn failed, or cancel is set (`cancel_event` / `run_control.is_cancelled`) | loop exits, status stays `active` |

The last row is deliberate: cancellation and provider failures pause the loop rather than consuming the goal, because the continuation turns share the caller's cancel token — a continuation is an ordinary turn and the Stop button already reaches it.

Ordering inside one iteration: met wins first (a final turn that achieves the goal without tool calls is a success, not idle spin), then judge-failure accounting, then the idle-spin check, then the checklist-stall check, then the cap.

Goal sessions and the `turn.stop` gate divide the stop decision cleanly: a session with a goal (active or waiting) never enters `continue_stop_hook_turns` — its goal loop is the sole stop decider, and the only external intervention is `/goal clear`. The `turn.stop` gate is the extension point for sessions **without** a goal (see `docs/reference/design/proactive/event-layer.md`).

## Events and surfaces

Every status change and every pre-continuation progress tick goes through `_emit_goal_update`: a `chat_response` envelope `{"type": "goal_update", "session_id", "goal": {…}}` on the dispatcher's `on_event` stream, plus a top-level `goal_update` WS broadcast via the webui server (best-effort — absent server, e.g. bare CLI or tests, is a no-op).

- **Web**: `session_loaded` carries the goal (`ws_actions/session.py`) for hydration; the composer's `GoalChip` (`web/components/chat/goal-chip.tsx`, shared `useSessionGoal` hook) renders `◎ goal · N/M` from that plus live `goal_update` frames (delivered through `use-ws`'s catch-all `op:ws-message` event). While `status === "waiting_user"` a **question panel** (`composer/question-panel.tsx`, shared with `ask_user_question`'s ask/confirm decisions — real asks take priority) grows UPWARD from the top of the input box: a one-line badge ("goal · 等你回答", Target icon), the question, and the option pills with their descriptions. Everything else stays put — textarea, bottom bar and env-chip row do not move or change; the transcript's bottom padding tracks the composer's measured height (`--main-composer-height` CSS variable, ResizeObserver) so the last message is never covered. Picking a pill or typing in the normal input sends the answer through the normal chat send path — exactly a typed message, so the loop's resume rule needs nothing special (real asks route through `question_reply` instead). The panel is driven by goal state, so a reload while parked re-shows it. Composer-typed `/goal …` is executed backend-side by the local-builtin branch in `ws_actions/chat.py`: a status/clear reply returns as a `local_command` envelope rendered as a transient system row; a set replaces the turn text with the goal directive and falls through into the normal turn flow.
- **Commands registry**: `/goal` is a `builtin`-layer command with a callable handler (`registry.register_shared_builtins`), so it lists in `/api/commands` and resolves for any host. The Rich REPL shadows it in its own process with a marker action (`cli/repl/handlers.py:_handle_goal`) that prints locally and launches the set-form's first turn through `process_user_turn` — the REPL's bare `rt.exec` turn runner bypasses the dispatcher and would never reach the loop.

## Implementation status

Implemented as described. Known ceilings: `local_command` replies in the web transcript are not persisted (parity with REPL console prints); concurrent turns on one session from two surfaces could each run the judge — `turns_used` is re-read per iteration so the cap still holds, and per-session serialization elsewhere (composer lock, follow-up lock) makes the race practically unreachable.

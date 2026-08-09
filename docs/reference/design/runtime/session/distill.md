# Distill — sessions into reusable procedures

A session records work that succeeded. Distilling converts that record into an artifact the next run can start from: a `SKILL.md` or an `@agentic_function`.

The design is deliberately thin. One new module renders a session as text; everything else is existing infrastructure, and the reasoning that makes a good procedure lives in a skill body rather than in code.

## Components

| Piece | Where | What it does |
|---|---|---|
| Transcript renderer | `openprogram/store/session/transcript.py` | `render_read_conversation()` — one branch of a session as LLM-readable plain text |
| Model-facing tool | `openprogram/functions/tools/read_conversation/` | `read_conversation` — the renderer as a `@function`, defaulting to the current session |
| The distill skill | `openprogram/skills_bundled/distill/` | Instructions for extracting a procedure and writing it out |
| Product page | `docs/capabilities/distill.md` | User-facing documentation |

## The transcript renderer

`get_branch` returns the conversational chain — user and assistant nodes linked by `predecessor`. Tool and function-call nodes are not on that chain; they hang off the assistant turn that issued them via `caller` ([DAG overview](../dag/overview.md)). The renderer joins the two: walk the branch, and under each turn print the calls whose `caller` points at it. This is the same edge `graph_builder` follows for the web DAG, read for prose instead of coordinates.

Signature:

```python
render_read_conversation(
    session_id,
    head_id=None,               # default: the session's active head
    include_function_calls=True,
    max_chars=60_000,
    store=None,                 # default: agent.session_db.default_db()
) -> str
```

The store comes from `default_db()` rather than a hardcoded `~/.openprogram` path, so a project-bound session resolves through the same locator as everything else. The `store` parameter exists for tests.

Each call prints its name, a success or `FAILED` marker, clipped arguments, and a clipped result. Failures are kept rather than filtered: a failed call and its correction is what a trap looks like in the record, and it is the highest-value material in a distillation.

Two nodes get explicit labels rather than passing as ordinary turns, because in both cases the reader would otherwise draw a wrong conclusion from what is present:

- **Compaction summaries** (`context/summary`) stand in for a collapsed range. Labeled so the reader knows detail was dropped there, not that the session was thin.
- **Spawn branch roots** open a sub-branch. Labeled so a nested agent's work is not read as the main thread.

Truncation is layered. Per-field caps stop one runaway tool result (a large file read) from evicting the reasoning around it; the total budget cuts at the last whole turn that fits and names how many turns were dropped, so a truncated transcript never looks like a complete one.

`tools/dag_dump.py` remains the debugging view — node ids, lanes, tiers. The two do not overlap.

## Discovery reuses existing tools

`list_agents` (`functions/tools/send_message/`) already enumerates session ids and `SID:HEAD` branch tips. Those are exactly the arguments `read_conversation` takes, so no listing tool is added. The tool accepts a whole `SID:HEAD` string in `session_id` and splits it, since that is the form `list_agents` prints.

## The skill carries the judgment

Distillation is a judgment task: deciding what in a transcript generalizes and what was incidental to that day. Nothing about it is deterministic, so there is no `distill()` function — the skill body tells the agent what to extract (goal, preconditions, steps, decision points, traps) and it writes the output file with its own `Write` tool.

This follows the precedent set when the `create()` / `edit()` / `improve()` wrappers were removed from agentic programming: a function whose entire body is one LLM call plus a file write is a layer the agent does not need.

The skill also decides the output form. A procedure requiring runtime judgment becomes a `SKILL.md`; a mechanical one becomes an `@agentic_function` written per the [agentic-programming](../../function/calling-unification.md) conventions.

## Output lands in the existing skill pipeline

A distilled skill is written to `~/.openprogram/skills/<name>/` or `<cwd>/skills/<name>/` — two of the five sources the loader already merges. It is therefore live without a restart (the watcher hot-reloads) and automatically available as `/<name>` (`commands/_skill_adapter.py` projects every discovered skill into the slash-command registry).

That is the reason the feature needs no subsystem of its own: the storage, the discovery, the reload, and the invocation path all exist. Distillation only had to produce a file in the right place.

## Revision closes the loop

Distillation covers refinement, not only first creation. The skill body directs the agent, before writing, to look for an existing skill on the same topic — same goal and preconditions, not merely a similar name — and to revise a match in place: keep what held, replace what the new session disproved, merge new decision points and traps into the flow. A distilled `@agentic_function` follows the same rule and is edited rather than duplicated, keeping its name stable for callers.

This is the refine step of the record → replay → refine loop. A skill that fails in use is corrected through the same path — the failing run is itself a session to distill — and the complaint form ("that skill didn't work, update it with what we learned") is among the triggers in the skill's description, so it routes here without a separate mechanism.

Revision adds no machinery. Finding the existing skill uses the loader's lookup locations and `openprogram skills list`; editing uses the agent's own tools; history lives in git, and the skill body forbids changelogs in the prose. Like distillation itself, revision is judgment carried by the skill, not code.

## Naming

The product word is **distill**, not "workflow". In this repository "workflow" already denotes an agentic program — a complete pre-built agent harness, documented under `docs/capabilities/workflows/`. A distilled procedure is a smaller thing: knowledge extracted from one session, not a shipped program. Reusing the word would have merged two concepts that appear side by side in the same documentation tab.

## Implementation status

Implemented as described.

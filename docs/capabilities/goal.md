# Session goals

A goal is a condition the session keeps working toward. You set it once with `/goal`; after every turn the runtime checks whether the condition holds, and while it does not, the agent continues on its own — each continuation is a normal turn you can watch, steer, or stop. The session does not go quiet just because the model stopped talking.

```
/goal all unit tests pass and the README documents the new flag
```

This stores the goal on the session and immediately starts a first turn with the condition text as the instruction. From then on, whenever a turn ends and the goal is still unmet, the runtime sends a follow-up turn (`[goal] 未达成：<reason>。继续。`) carrying the judge's reason for why it is not done yet. Continuation turns always have web search available on top of the session's tool settings — they run unattended, so they get the search tool you could have toggled on; your session settings themselves are not changed.

## Your one-liner is refined automatically

A single sentence cannot cover everything a judge needs, so right after you set a goal a background step expands it into a full specification: a checklist of verifiable completion criteria (including process requirements such as "read these sources before writing that section"), explicit boundaries for what is out of scope, and the checklist the judge walks item by item. It may glance at the working directory to understand the task context. The result appears as a system row in the transcript, so you see exactly what the system understood the goal to be — if it misread your intent, `/goal clear` and set the goal again with more detail. Your original text is kept unchanged; judging uses the refined specification, and falls back to your text if the refinement has not finished or failed. The refinement never delays the goal: the first working turn starts in parallel.

## How a goal is judged

One decision agent decides. After each turn, a spawned judge turn reads the goal text plus a compacted view of the conversation (the active summary when one exists, plus the latest turns and tool results) and answers a strict yes/no with a reason. It has inspection tools (bash, read, grep, glob, list) and checks the working directory when it deems that necessary — the judgment is entirely its own, and only its "met" counts as completion. The judge is deliberately not the working agent asking itself "am I done?" — self-reports run optimistic; the verdict comes from a separate context. The whole judgment is one agentic function — `goal` in the Programs panel — so you can run it by hand against any goal text and see exactly how a verdict is reached. The judge may also decide the run must pause and ask you something; how freely it asks depends on whether the session is attended, and it can ask at most once per hour — further questions inside that window make it pick the most reasonable option itself and continue with the decision written down. A goal session stops only through this judge or `/goal clear`; the `turn.stop` hook gate applies to sessions without a goal.

## Writing a condition that works

Write the condition as something the transcript can prove, not a feeling:

- Good: "`tests/unit` passes and the new page is registered in `nav.py`" — the judge can see test output and file edits.
- Good: "the script prints `OK` for all 12 inputs" — provable by output.
- Weak: "the code is clean" / "the feature works well" — nothing in the transcript settles it, so the judge guesses and the loop wanders.

## Status, clearing, stopping

```
/goal            # show the goal, its status, turns used, and the last reason
/goal clear      # remove it (aliases: stop / off / cancel)
```

The Stop button (and any cancel) works during continuation turns exactly as during your own turns — a continuation is an ordinary turn. Stopping does not clear the goal; it stays `active` and resumes judging after your next message. Use `/goal clear` to actually remove it.

In the web UI an active goal shows as `◎ goal · N` in the chip row above the composer (N = turns used so far; `N/M` when you set a turn cap). It flips to `goal achieved` / `goal capped` / `goal error` when the loop ends.

## When the loop stops by itself

| Outcome | Meaning |
|---|---|
| `achieved` | The decision agent answered met. |
| `capped` | The goal used its turn budget — only possible when you set one (setting `goal.max_turns`; empty by default = no cap). Raise the budget or split the goal. |
| `error` | A continuation turn did no tool work while the goal stayed unmet (spinning), or the judge failed three times in a row. The goal state and last reason stay visible in `/goal`. |
| `cleared` | You removed it. |

A failed turn (provider error, cancellation) pauses the loop without consuming the goal — the goal stays `active` and judging resumes after the next completed turn.

## Where it works

`/goal` is a built-in command: the web composer, the TUI, and the Rich REPL all accept it, and the continuation loop runs in the worker, so it keeps going even if you close the tab. Goals set in one surface are visible from every other one — the state lives on the session.

# Session goals

A goal is a condition the session keeps working toward. You set it once with `/goal`; after every turn the runtime checks whether the condition holds, and while it does not, the agent continues on its own — each continuation is a normal turn you can watch, steer, or stop. The session does not go quiet just because the model stopped talking.

```
/goal all unit tests pass and the README documents the new flag
```

This stores the goal on the session and immediately starts a first turn with the condition text as the instruction. From then on, whenever a turn ends and the goal is still unmet, the runtime sends a follow-up turn (`[goal] 未达成：<reason>。继续。`) carrying the judge's reason for why it is not done yet.

## How a goal is judged

An LLM judge decides. After each turn, one no-tools call on the session's model reads the goal text plus the tail of the conversation (last assistant output and recent tool results) and returns a strict yes/no with a reason. The judge is deliberately not the working agent asking itself "am I done?" — self-reports run optimistic; the verdict comes from a separate call that only sees evidence. Before the loop stops on a met verdict, a spawned verifier agent re-checks the claim against the working directory with inspection-only tools.

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

In the web UI an active goal shows as `◎ goal · N/20` in the chip row above the composer (N = turns used so far). It flips to `goal achieved` / `goal capped` / `goal error` when the loop ends.

## When the loop stops by itself

| Outcome | Meaning |
|---|---|
| `achieved` | The judge answered met and the verifier confirmed it. |
| `capped` | The goal used its turn budget (default 20; setting `goal.max_turns`). Raise the budget or split the goal. |
| `error` | A continuation turn did no tool work while the goal stayed unmet (spinning), or the judge failed three times in a row. The goal state and last reason stay visible in `/goal`. |
| `cleared` | You removed it. |

A failed turn (provider error, cancellation) pauses the loop without consuming the goal — the goal stays `active` and judging resumes after the next completed turn.

## Where it works

`/goal` is a built-in command: the web composer, the TUI, and the Rich REPL all accept it, and the continuation loop runs in the worker, so it keeps going even if you close the tab. Goals set in one surface are visible from every other one — the state lives on the session.

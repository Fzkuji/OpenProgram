# Guide

This directory is the home of OpenProgram's **own programming model** — the
concepts you will not find in a generic LLM-framework tutorial, collected in
one place. If you are writing functions for OpenProgram (or deciding whether
to), read here first; the generic project docs (install, API index,
troubleshooting) live in the other tabs. For the paradigm's background, see
the [philosophy](philosophy.md).

## Learning path

Read in order; each step builds on the previous one.

| # | Doc | What it teaches |
|---|---|---|
| 1 | [`philosophy.md`](philosophy.md) | Why "agentic programming" — the rationale behind the model |
| 2 | [`writing-functions/agentic-function.md`](writing-functions/agentic-function.md) | `@agentic_function`: wrap a Python function whose body runs LLM calls via `runtime.exec()`; composition patterns |
| 3 | [`writing-functions/function-metadata.md`](writing-functions/function-metadata.md) | Parameter descriptions, placeholders, hidden arguments, `render_range` — the source of truth for function metadata |
| 4 | [`writing-functions/pure-python.md`](writing-functions/pure-python.md) | When NOT to use the decorator: plain deterministic helpers |

## Choosing the next step

OpenProgram gives you three ways to decide "what runs next" inside a
function. They are not alternatives to learn once and forget — picking the
right one per task is the core skill:

| Doc | Mechanism | Use when |
|---|---|---|
| [`choosing-the-next-step/fixed-order-calls.md`](choosing-the-next-step/fixed-order-calls.md) | Python code calls sub-functions in a fixed order | The step order is known ahead of time (pipelines: draft → review → revise) |
| [`choosing-the-next-step/tool-calling.md`](choosing-the-next-step/tool-calling.md) | Provider-native tool use: the model picks a function each turn, loop until it answers in text | Open-ended work where the model decides how many and which calls to make |
| [`choosing-the-next-step/next-step-decision.md`](choosing-the-next-step/next-step-decision.md) | `decision.make(prompt, options)` / `runtime.exec(..., choices=...)`: a text menu of options, the choice itself resolves into the next result | Routing / finite branches; options may be plain values, not just functions; no provider tool-use support needed |

## Reference

- [`../../reference/api/agentic-function.md`](../../reference/api/agentic-function.md) — decorator API quick reference
- [`../../reference/api/runtime.md`](../../reference/api/runtime.md) — `Runtime.exec()` parameters and behaviour
- [`../../../skills/agentic-programming/SKILL.md`](https://github.com/Fzkuji/OpenProgram/blob/main/skills/agentic-programming/SKILL.md) — the in-repo skill an agent follows to write/validate functions (file layout, rule checklist, smoke tests)
- [`../../reference/design/function/function-calling-unification.md`](../../reference/design/function/function-calling-unification.md) — internal design notes on the function-calling framework (evolution / refactor plans, not needed for authoring)

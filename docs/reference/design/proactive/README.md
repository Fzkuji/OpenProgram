# Proactive Layer — Design

This adds a layer of "proactivity" to OpenProgram: while the agent is working, the framework watches on its own and steps in when it should — blocking dangerous commands, reminding you of tests that need to be added, noticing when the model is stuck. The user doesn't have to say a word, and the framework still acts.

The core of this layer is **event-driven**: every thing that happens while the agent works (user sends a message, model replies, calls a tool, a tool fails, a file gets changed) is recorded as an "event"; your proactive rules aren't welded to some fixed location, but instead "subscribe" to these events — when an event fires, they're woken up, make a judgment, and decide whether to step in.

## This layer splits into two parts: event foundation + proactivity application

- **Event foundation** (the bedrock, used by the whole framework): one unified event stream that anyone can subscribe to. proactive is just its first consumer; webui and other future features can use it too.
- **Proactivity application** (built on the foundation): rules (Policy) subscribe to the event stream and step in when they should.

The two parts are decoupled. You can build just the foundation and hold off on the rules.

## How to read

**Read the event foundation first** (this is exactly what you've been validating recently — whether the system actually supports "do something at some moment"):

0. **[`event-reference.html`](event-reference.html)** — **the official API Reference**: every event type (26 of them, across the three categories A / B / ws.frame) listed one by one, each with a payload field table, trigger timing, and source file:line; the full API and all three usages (observe / intercept / ask). Searchable and expandable. Double-click to open in a browser. **Check it first when looking up an event.**
1. [`event-layer.md`](event-layer.md) — **the unified Event model + where this layer sits in the framework + the architecture diagram**. What an event looks like, the two major classes of event sources (agent at work / system state), where the bus lives, and who it interacts with.
   **Visual version: [`event-layer.html`](event-layer.html)** (a real SVG architecture diagram + event-flow animation, double-click to open in a browser); the md is the text source of the same content.
2. [`framework-evolution.md`](framework-evolution.md) — **how signals are routed across the framework**. Why routing through webui leaves gaps, what the bus as the hub gives each role, what each subsystem contributes, and what the design leaves untouched.
   **Visual version: [`framework-evolution.html`](framework-evolution.html)**.

**Then read the proactivity application** (the proactive layer built on top of the foundation):

2. [`overview.md`](overview.md) — walk through one scenario end to end (the model wants to run `rm -rf`, and how the framework blocks it); concepts like rules, ways of stepping in, and state are explained in place within the story.
3. [`events-and-state.md`](events-and-state.md) — how state is "accumulated" (folded) out of events; this is the principle that lets rules "remember the past".

**Want to get hands-on or discuss the details**:

| Doc | What it covers |
|---|---|
| [`execution-model.md`](execution-model.md) | How rules (Policy) are written; the difference between the "blocking" and "observing" kinds |
| [`policies-mvp.md`](policies-mvp.md) | Three concrete rules, to use as templates for writing new ones |
| [`invariants.md`](invariants.md) | The bottom lines the framework must hold (mainly "don't let the framework trigger itself and spiral into an infinite loop") |

## A one-line answer to a few questions you might ask

| Question | Answer |
|---|---|
| Is this layer a new framework or a patch? | A standalone layer, but it reuses existing mechanisms (the event bus, tool approval, background tasks) instead of starting from scratch |
| What are rules written in? | Plain Python classes, not a config file / DSL. If you can write Python, you can write rules |
| Will it be heavyweight? | The scope is **the bedrock only** — events, state, rules, stepping in. The research-grade concerns (tamper resistance, offline replay verification, adversarial safety) sit outside that scope, in `_research_archive/` |

## Implementation status

The event foundation is in place: the bus is enabled, both classes of source emit into it,
`tool.before` carries the synchronous interception point, and webui consumes the bus as an
ordinary subscriber. The proactive rule layer described in `execution-model.md` and
`policies-mvp.md` is the part not yet built. The event log is always on: send a message, then read
`~/.openprogram/sessions/<sid>/events.jsonl` (or `~/.openprogram/logs/events.jsonl`
for events outside a session).

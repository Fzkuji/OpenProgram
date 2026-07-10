# Key Features — detailed tour

The README's [Key Features](../README.md#key-features) table
points here for the longer story behind each one. The
[Agentic Programming philosophy](philosophy/agentic-programming.md)
note covers the *why*; this page covers the *how it shows up
in everyday use*.

## Automatic Context

Every `@agentic_function` call creates a **Context** node.
Nodes form a tree that is automatically injected into LLM calls:

```
login_flow ✓ 8.8s
├── observe ✓ 3.1s → "found login form at (200, 300)"
├── click ✓ 2.5s → "clicked login button"
└── verify ✓ 3.2s → "dashboard confirmed"
```

When `verify` calls the LLM, it automatically sees what
`observe` and `click` returned. No manual context management:
you write functions, the runtime threads the tree.

## Deep Work — autonomous quality loop

For complex tasks that demand sustained effort and high
standards, `deep_work` runs an autonomous plan-execute-evaluate
loop until the result meets the specified quality level:

```python
from openprogram.functions.agentics.deep_work import deep_work

result = deep_work(
    task="Write a survey on context management in LLM agents.",
    level="phd",        # high_school → bachelor → master → phd → professor
    runtime=runtime,
)
```

The agent clarifies requirements upfront, then works fully
autonomously — executing, self-evaluating, and revising until
the output passes quality review. State is persisted to disk,
so interrupted work resumes where it left off.

## Functions that author functions

Writing, fixing and scaffolding `@agentic_function`s is itself
agent work — done with ordinary file-editing tools, guided by
the **`agentic-programming` skill**
([`skills/agentic-programming/SKILL.md`](../skills/agentic-programming/SKILL.md)).
There are no dedicated `create()` / `fix()` framework calls:
they only ever wrapped one LLM call plus a file write, which an
agent does directly.

The skill is the complete spec — where the file goes, the
decorator's metadata, the docstring vs `content` split, a
rule-based validation checklist, and a smoke test. An agent
reads it, writes the function, validates it, runs it; the
`write → run → fail → fix` cycle still means programs improve
through use.

## Conversation as a git DAG

Session history is stored like a git repository, not a flat
list. Every exchange is a commit, branches are first-class, and
the right sidebar exposes the usual git operations:

- **Branch off** any past exchange to explore an alternative
  without losing the original thread
- **Attach** context from another session (cross-session reuse)
  as a labelled user message
- **Merge** two threads when their branches converge
- **Cherry-pick** specific commits across branches

Branches that touch files run in **isolated git worktrees**
under the hood, so two concurrent agents on different branches
can't fight over the same source tree. Other frameworks fork
conversations by copying messages; we fork the underlying repo.

## Layered memory

Memory isn't a single bag. Six separate stores under
`~/.openprogram/memory/` cover different timescales and purposes:

| Layer | What goes there |
|---|---|
| `journal` | Short-term — recent observations, raw notes |
| `wiki` | Durable — facts the agent decided to keep around |
| `sleep` | Periodic consolidation (offline daemon merges journal → wiki) |
| `scheduler` | Cron-driven recalls that surface a memory at a specific time |
| `recall_counts` | Hit counts that boost frequently-used memories |
| `store` | Project-scoped key/value |

Open `/memory` to inspect or hand-edit any layer; the agent
decides which layer to write to based on what it learned. The
split exists because "remember this until I tell you to forget"
and "remember this for the next 10 turns" want different
storage strategies.

## Mini-DAG — execution view in the right rail

Every conversation has a right-rail mini-DAG that draws each
node (user message, LLM call, code Call, attach) and the edges
between them. The view scrolls with the chat: clicking a node
scrolls the conversation to the corresponding message, and the
panel keeps the currently-viewed range highlighted. d3-hierarchy
layout is available behind a toggle for fan-out-heavy traces;
see [`design/runtime/dag/dag-rendering.md`](design/runtime/dag/dag-rendering.md) when adding new
node kinds.

## Multi-account + key rotation

One provider, several accounts — and several keys per account — managed the same
way from every surface. An **account is a profile**: an independent set of
credentials for a provider.

```bash
openprogram providers login openai --profile work      # add a second account
openprogram providers login openai --profile personal
openprogram providers use openai work                  # run openai on "work"
openprogram providers use openai                        # back to the default account
openprogram providers list                              # the active one is marked
```

The same panel lives in the **web** (Settings → Providers) and the **TUI**
(`/login <provider>`): list / add / activate / rename / remove. `/login` in the
terminal completes the whole sign-in there — OAuth, device-code, import-from-CLI,
or an API-key paste — instead of bouncing you to the browser. claude-code keeps
its Claude-subscription (Meridian) backend behind the exact same panel, so it's
just one instance of the generic surface.

**api-key providers** get the same multi-credential model as a list of keys:
paste a key (it's validated first) and it joins the list, **name** each one, and
pick which is **active** (the one that's used) with *Use*. That's the same
"several credentials, switch between them" idea OAuth providers have for
accounts — just keys instead of logins. **Rotation is an optional toggle**, off
by default: leave it off and only the active key is called; turn it on and a
rate-limited key cools down while the next takes over (`429` → cooldown + rotate,
`402` longer for billing, `5xx` briefly), with a strategy picker (`in order` /
`spread evenly` / `random` / `least used`) and ↑ / ↓ priority. A key you'd
already set the old way (env var / config) is migrated into the list so nothing
is lost. Design + status:
[`design/providers/auth/unified-account-management.md`](design/providers/auth/unified-account-management.md).

## Multi-agent + multi-channel (where this is going)

The dispatcher already supports multiple `agent_id`s per
session — every row is stamped with the producer agent, the
sidebar can colour-code by author, and the channel layer maps
external transports (currently Discord) to per-account
identities. Cross-channel message routing + a declarative
tool-availability system are tracked as the next set of
features (see the project's open task list for status).

# Features

The README's [Detailed features](../README.md#detailed-features) table
points here for the longer story behind each one. The
[Agentic Programming philosophy](../capabilities/agentic-programming/philosophy.md)
note covers the *why*; this page covers the *how it shows up
in everyday use*.

## Automatic Context

Every `@agentic_function` call is recorded as a node in the
session's flat conversation DAG — the same DAG that holds user
messages and LLM calls. Nested calls thread automatically:

```
login_flow ✓ 8.8s
├── observe ✓ 3.1s → "found login form at (200, 300)"
├── click ✓ 2.5s → "clicked login button"
└── verify ✓ 3.2s → "dashboard confirmed"
```

When `verify` calls the LLM, it automatically sees what
`observe` and `click` returned. No manual context management:
you write functions, the runtime threads the DAG.

Two decorator knobs control what a call contributes to later
LLM calls:

```python
@agentic_function(expose="full", render_range={"callers": 1})
def navigate(target): ...
```

`expose` sets how much of the call's internals later calls see —
`io` (default: input/output only), `llm` (only its LLM exchanges),
`full` (everything), or `hidden` (no node at all).
`render_range={"callers": N}` caps how much pre-existing history
the function itself sees (`0` walls it off completely);
`{"subcalls": N}` bounds its own in-frame history in long loops.

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
([`skills/agentic-programming/SKILL.md`](https://github.com/Fzkuji/OpenProgram/blob/main/skills/agentic-programming/SKILL.md)).
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
- **Merge** two or more branches into a single aggregated reply

Branches that touch files run in **isolated git worktrees**
under the hood, so two concurrent agents on different branches
can't fight over the same source tree. Other frameworks fork
conversations by copying messages; we fork the underlying repo.

## Layered memory

Memory isn't a single bag. `~/.openprogram/memory/` holds
distinct pieces, each with its own timescale and purpose:

| Piece | What it is |
|---|---|
| `journal/` | Chronological notes, one Markdown file per day |
| `wiki/` | Durable knowledge — an Obsidian-style vault of topic pages, plus an LLM-maintained `index.md`, a `log.md` timeline, and `reflections.md` |
| `core.md` | Tiny (<2 KB) always-on block injected into every agent's system prompt |
| `index.sqlite` | Full-text (FTS5) index over wiki + journal, used for recall |
| `.state/` | Bookkeeping — recall hit counts, sleep-stage state |

Consolidation runs as a "sleep" sweep (light → deep → REM) that
merges journal entries into the wiki and rewrites `core.md`;
`openprogram memory sleep` runs one now. Inspect or hand-edit
from the CLI (`openprogram memory status / recall / show / edit`)
or the web UI's Memory page. The split exists because "remember
this permanently" and "what happened yesterday" want different
storage strategies.

## Mini-DAG — execution view in the right rail

Every conversation has a right-rail mini-DAG that draws each
node (user message, LLM call, code Call, attach) and the edges
between them. The view scrolls with the chat: clicking a node
scrolls the conversation to the corresponding message, and the
panel keeps the currently-viewed range highlighted. The
rendering rules are specified in
[`design/runtime/dag/dag-rendering.md`](../reference/design/runtime/dag/dag-rendering.md) — consult it when
adding new node kinds.

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
or an API-key paste — instead of bouncing you to the browser. Claude-subscription
accounts (`claude-code`) sit behind the exact same panel — just one instance of
the generic surface.

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
[`design/providers/auth/unified-account-management.md`](../reference/design/providers/auth/unified-account-management.md).

## Multi-agent + multi-channel (where this is going)

The dispatcher already supports multiple `agent_id`s per
session — every row is stamped with the producer agent, the
sidebar can colour-code by author, and the channel layer maps
external transports (Telegram / Discord / Slack / WeChat) to
per-account identities. Cross-channel message routing + a
declarative tool-availability system are tracked as the next
set of features.

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

## Functions that author functions

Writing, fixing and scaffolding `@agentic_function`s is itself
agent work — done with ordinary file-editing tools following
the [agentic function API](../reference/api/agentic-function.md).
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

## Memory that writes itself

Memory lives in one place, `~/.openprogram/memory/`, and all of it
is Markdown you can open in any editor.

| Path | What it holds |
|---|---|
| `core.md` | A short always-on block, injected into every session's system prompt |
| `topics/` | One file per subject, such as `topics/people/dave.md`. Every paragraph carries an ID and cites where the fact came from |
| `sources/` | The conversation turns those citations point at. Added to, never rewritten |
| `timeline/` | The same facts arranged by date, rebuilt from `topics/` |
| `.scriptorium/` | Bookkeeping: how far each conversation has been read, and the lock that keeps two writers apart. Not memory |

Nothing is written per turn. Finished turns collect until there is
enough to be worth a pass, and then one pass decides which subject
each fact belongs to and writes it there. Where a fact was said is
not where it is stored, so a detail about Dave lands in
`topics/people/dave.md` however many conversations it took to
learn. A conversation that goes quiet for half an hour is written
whatever its size, so a short exchange is not left waiting for a
batch that never comes.

Every write lands whole or not at all. An edit that cites a source
it did not supply, points at a paragraph that does not exist, or
breaks the topic format is refused, and the workspace is left byte
for byte as it was. Two writers never interleave: a background
write that finds the workspace busy gives up after a second rather
than making you wait, and comes back on the next pass.

Writing only ever makes files longer, so at 03:00 a second pass
splits a file that has grown to cover several subjects, merges
paragraphs that say the same thing, and repairs links.
`openprogram memory sleep` runs that pass now instead of tonight.

Read and repair it by hand from the CLI:

```bash
openprogram memory status                        # where it is, what it holds, its revision
openprogram memory recall xelatex thesis         # search and print the matching paragraphs
openprogram memory show topics/people/dave.md
openprogram memory edit topics/people/dave.md    # $EDITOR; the edit lands only if it validates
openprogram memory export                        # tar.gz the whole workspace
```

The web UI's Memory page reads the same workspace. Agents reach it
through `memory_search`, `memory_grep`, `memory_get`,
`memory_browse`, `memory_update` and `memory_status`. There is no
"save this" tool: recording the conversation is already happening
in the background, and `memory_update` is for correcting what is
there or writing down something you asked to be remembered right
now.

## Mini-DAG — execution view in the right rail

Every conversation has a right-rail mini-DAG that draws each
node (user message, LLM call, code Call, attach) and the edges
between them. The view scrolls with the chat: clicking a node
scrolls the conversation to the corresponding message, and the
panel keeps the currently-viewed range highlighted. The
rendering rules are specified in
[`design/runtime/dag/rendering.md`](../reference/design/runtime/dag/rendering.md) — consult it when
adding new node kinds.

## Multi-account + key rotation

One provider, several accounts — and several keys per account — managed the same
way from every surface. An **account is a profile**: an independent set of
credentials for a provider.

```bash
openprogram providers login openai --account work      # add a second account
openprogram providers login openai --account personal
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

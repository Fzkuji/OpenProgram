---
name: distill
description: "Turn the experience in a conversation into something reusable — read a session (this one or a past one) and write it out as a SKILL.md or an @agentic_function, or revise an existing one with what the session taught, so the same work can be repeated later without rediscovering it. Triggers: 'distill this session', 'turn this into a skill', 'save this as a reusable procedure', 'remember how we did this', 'make this repeatable', 'extract the workflow from that conversation', 'write this up as a skill', 'that skill didn't work — update it with what we learned', 'improve the skill from this session', 'fold this experience into the existing skill'."
---

# Distill — turn a session into something reusable

A session is a record of work that already succeeded: the decisions, the
commands, the wrong turns that got corrected. Distilling it means writing
down what generalizes, so the next run starts from the finished procedure
instead of the blank page.

You do this with your own `Read` / `Write` / `Edit` tools. There is no
`distill()` function to call — the judgment about what generalizes is the
whole task, and that is yours.

## 1. Get the material

**Distilling the current conversation** — you already have it. Read back
over the turns; do not re-read them through a tool.

**Distilling a past session** — read it with the `read_conversation`
tool. It prints each turn's content plus the tool calls that turn made,
with arguments, results, and whether each one failed.

```
list_agents(scope="all")                  # find the session id
read_conversation(session_id="local_…")   # read its active branch
```

`list_agents` gives each conversation as a `SID:HEAD` address. If the work
happened on a side branch (a retry, a fork), pass the HEAD half as
`head_id`. If the transcript comes back truncated and the dropped turns
matter, re-read with a larger `max_chars`.

The user may name the session only vaguely ("the one where we fixed the
proxy"). `list_agents` with the default `scope="session"` shows the
current session's branches with previews; `scope="all"` reaches other
sessions. Confirm with the user before distilling the wrong one.

## 2. Decide what actually generalizes

Read for the *procedure*, not the narrative. A session contains a lot
that is specific to that day — a particular file path, a one-off typo,
the user changing their mind. Distillation keeps what would be true the
next time.

Extract five things:

| | What to look for |
|---|---|
| **Goal** | What was being accomplished, stated so someone with a similar task recognizes it. |
| **Preconditions** | What has to be true for this to apply — a tool installed, a repo layout, credentials, a service running. If the procedure silently assumes something, name it. |
| **Steps** | The sequence that worked. Concrete commands and file paths where they are stable; described in general where they were incidental. |
| **Decision points** | Where the path forked and *what made the choice*. "If the build fails with X, do Y; otherwise Z." This is the highest-value part and the part most often lost. |
| **Traps** | What went wrong before it went right. A failed tool call in the transcript is the evidence: record the mistake and the correction, not just the correction. |

Also note which tools and functions were used — a procedure that depends
on a tool that may not be available should say so.

If the session contains no reusable procedure — a one-off question, a
conversation that never converged — say so and stop. A skill that
describes nothing repeatable is worse than no skill.

## 3. Choose the form

Two products. The test is whether executing the procedure requires
judgment.

**Write a SKILL.md when the procedure needs a model to read the
situation.** Steps that branch on what the output looks like, that
involve reading code or prose and deciding, that call for taste. The
skill is instructions for a future agent.

**Write an `@agentic_function` when the procedure is mechanical.** A
fixed sequence with known inputs and outputs, where the only reasoning is
inside individual steps (summarize this, classify that). Then load the
`agentic-programming` skill and follow its spec — prompts go in the
docstring, the flow goes in Python.

Mixed cases are common: a mechanical core with a judgment-heavy wrapper.
Write the function for the core and a short skill that says when to reach
for it.

## 4. Revise, don't duplicate

Distilling a topic that has been distilled before produces a revision,
not a sibling. Before writing, check whether a skill on the same topic
already exists: list what is currently discovered (`openprogram skills
list`) and look in `~/.openprogram/skills/` and `<cwd>/skills/`. "Same
topic" means the goal and the preconditions overlap — the existing
skill accomplishes the same thing under the same circumstances. A
coincidentally similar name is not a match, and a real match may sit
under a different name; judge by what the skill does, not what it is
called.

When a match exists, `Read` its `SKILL.md` and revise it in place:

- **Keep** the steps the new session confirmed. Do not rewrite what
  still holds.
- **Replace** what practice disproved. A step the session showed to be
  wrong is overwritten by what worked — the old advice goes away, not
  kept alongside as an alternative.
- **Merge** new decision points and traps into the flow where they
  belong, not appended at the end.

The revised body reads as if written in one sitting. No changelog, no
"updated" markers, no version history in the prose — git is the
history.

The same rule covers the function form: when the procedure was
distilled as an `@agentic_function`, edit that function's docstring and
body rather than writing a second function beside it, and keep its name
stable so existing callers still resolve.

A user complaint is the common entry point here. "That skill didn't
work — update it with what we learned this time" is a distillation of
the current session into the existing skill: the failing run is the
material, what the skill got wrong is the finding, and the correction
lands in the file that misled you.

## 5. Write the SKILL.md

Location decides who sees it:

| Where | Path | When |
|---|---|---|
| User level | `~/.openprogram/skills/<name>/SKILL.md` | The procedure travels with the user across projects. |
| Project level | `<cwd>/skills/<name>/SKILL.md` | The procedure is about this repository — its layout, its build, its conventions. |

Default to project level when the steps name paths inside the current
repo; user level otherwise. When it is genuinely ambiguous, ask.

Format:

```markdown
---
name: <lowercase-hyphenated-name>
description: "What it does and when to reach for it — the model matches on this sentence. Triggers: '<phrase a user would type>', '<another>'."
---

# <Name> — <one-line purpose>

<Goal and when this applies.>

## Prerequisites
<What must be true first.>

## Steps
<The procedure, with decision points inline.>

## Traps
<What goes wrong and how to tell.>
```

`name` and `description` are both required — a directory missing either
is skipped at load. `name` must match the directory name.

Quality rules, in order of how often they are violated:

1. **Write the judgment, not just the action.** "Check whether the branch
   diverged; if it has, rebase before pushing" beats "run git rebase".
   A step whose *when* is missing is a step a future agent will apply at
   the wrong moment.
2. **Say what to do when it fails.** Every step that can fail gets its
   failure mode named.
3. **No narrative.** "Then we tried X, which didn't work, so we tried Y"
   is a diary. Write "Use Y. X fails because …".
4. **Name things fully.** Full words in names, and point at the specific
   file, command, or service rather than "the config" or "the script".
5. **Keep it short enough to be read.** A skill is loaded into a model's
   context on demand. Cut anything that does not change what the reader
   would do.

Write the skill in English.

## 6. Tell the user it is live

Skills are hot-reloaded: the new directory is picked up without a
restart, and it is projected into the slash-command registry, so it is
immediately available both ways —

- the model loads it on its own when a task matches the `description`
- the user can type `/<name>` to insert it

Report the path you wrote, the name, and one sentence on when it will
fire. If the description is what decides when it fires, show the
description too — that is the line the user will want to tune. For a
revision, report what changed and which old advice was dropped.

## Verifying

Reread the finished skill and ask: could someone who was not in the
original session follow this? If a step assumes context that only exists
in the transcript, that context belongs in the skill. That is the whole
check.

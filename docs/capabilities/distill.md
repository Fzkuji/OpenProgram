# Distill

Distilling turns a conversation that worked into something you can run again. You point the agent at a session — the current one or an old one — and it writes the experience out as a [skill](skills.md) or an [agentic function](agentic-programming/writing-functions/agentic-function.md), so the next time the same class of task comes up the procedure is already there.

Nothing new is installed. A distilled skill lands in the ordinary skill directories and is discovered by the ordinary loader; a distilled function is a normal `@agentic_function`.

## Running it

Ask in plain language, or use the slash command:

```
/distill
```

Either triggers the bundled `distill` skill. Typical phrasings that also fire it: "turn this into a skill", "save this as a reusable procedure", "make this repeatable", "extract the workflow from that conversation".

**The current conversation** needs no arguments — the agent already has it.

**A past session** is read with the `session_transcript` tool. You can name the session directly, or describe it ("the one where we set up the proxy") and let the agent find it with `list_sessions`. It will confirm before distilling.

## What comes out

The agent extracts the goal, the preconditions, the steps, the decision points (what made it pick one path over another), and the traps it hit along the way. Then it picks a form:

| Form | Written when | Lands in |
|---|---|---|
| A skill (`SKILL.md`) | Executing the procedure needs judgment — steps that branch on what the output looks like | `~/.openprogram/skills/<name>/` or `<cwd>/skills/<name>/` |
| An agentic function | The procedure is mechanical — a fixed sequence with known inputs and outputs | Wherever the [agentic-programming](agentic-programming/README.md) conventions put it |

Project-level (`<cwd>/skills/`) is the default when the steps name paths inside the current repository; user-level (`~/.openprogram/skills/`) when the procedure travels with you across projects. The agent tells you which it chose and where it wrote.

If the session contains no repeatable procedure, the agent says so instead of writing a hollow skill.

## Using what was distilled

A distilled skill is live immediately — skill directories are hot-reloaded, no restart. It reaches you two ways:

- **The model loads it on its own** when your task matches the skill's `description` line. That sentence is what decides when it fires, so it is the thing to tune if the skill triggers too often or never.
- **You invoke it directly** by typing `/<name>`, since every discovered skill is projected into the slash-command registry.

`openprogram skills list` shows it alongside the rest. Editing it later is just editing the file — see [Skills](skills.md) for the format and the lookup paths.

## Reading a session yourself

The tool the distill skill uses is available on its own. `session_transcript` renders a session's branch as plain text: each turn's content plus the tool calls that turn made, with arguments, results, and failures marked.

```
--- [2] assistant ---
Building the site first, then rsyncing.
  [call] bash -> ok
    args: {"command": "python -m tools.docs_site.build"}
    result:
      wrote 214 pages to docs/_site
  [call] bash -> FAILED
    args: {"command": "python -m tools.docs_site.checklinks"}
    result:
      2 dead links: capabilities/distill.md
```

It defaults to the current session's active branch. Pass `session_id` for another session (find ids with `list_sessions`), and `head_id` to read a side branch rather than the active one (find tips with `list_branches`). Long output is cut at the last whole turn that fits and says how many turns it dropped; raise `max_chars` if you need them.

Ask for it in words — "show me the transcript of that session" — or let the agent reach for it during distillation.

## Related

- [Skills](skills.md) — the `SKILL.md` format, the five lookup sources, and the management CLI
- [Agentic Programming](agentic-programming/README.md) — the function form, for procedures that need no judgment at runtime
- [Agentic workflows](workflows/README.md) — the full pre-built agent programs, a different thing from a distilled procedure

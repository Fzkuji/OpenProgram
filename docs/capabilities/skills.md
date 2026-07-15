# Skills

A skill is domain knowledge the model loads on demand: one directory containing a `SKILL.md`. This page covers the skill format, the lookup paths, and the management commands — how to teach the agent "how to do a class of tasks" without writing code.

## How it works

1. At startup, each skill directory is scanned for `<slug>/SKILL.md`; the `name` + `description` in the front matter are parsed.
2. Every skill's name, one-line description, and the absolute path of its `SKILL.md` are rendered into an `<available_skills>` block appended to the system prompt.
3. The model judges whether the current task matches a skill's description; on a match, it reads the full `SKILL.md` with the `read` tool. The full text is never injected automatically.

A skill executes nothing by itself. Scripts, reference files, and data can sit next to `SKILL.md` and be run through the existing `bash` / `execute_code` tools.

## Format

```markdown
---
name: my-skill
description: One line saying when to use it — the model matches on this sentence.
---

The body is free-form markdown: procedures, rule checklists, examples...
```

The front matter is a `key: value` YAML subset; `name` and `description` are both required — a directory missing either is simply skipped.

## Lookup paths

Two locations are probed in order by default; on a name clash the first one found wins, so **user skills override repo skills**:

1. `~/.openprogram/skills/` (user level)
2. `<OpenProgram repo>/skills/` (project level)

Plugins can also contribute skills (see [Plugins](plugins.md)); skills installed from a remote source land in the remote-cache source.

## Management commands

```bash
openprogram skills list       # list discovered skills by source (project / user / remote-cache)
openprogram skills doctor     # scan skill directories for problems
openprogram skills search <q>       # search discovery sources (ClawHub by default)
openprogram skills install <spec>   # install: slug (ClawHub by default), clawhub:<slug>, github:owner/repo
openprogram skills update     # compare local SKILL.md hashes with upstream, re-pull stale ones
openprogram skills remove <slug>    # remove an installed skill (project / user / remote-cache only)
```

`install` accepts `--source` to name a discovery-source URL (`clawhub://`, a GitHub repo, or a JSON index).

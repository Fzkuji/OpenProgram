# Skills

A skill is domain knowledge the model loads on demand: one directory containing a `SKILL.md`. This page covers the skill format, the lookup paths, and the management commands — how to teach the agent "how to do a class of tasks" without writing code.

## How it works

1. At startup, each skill directory is scanned for `<slug>/SKILL.md`; the `name` + `description` in the front matter are parsed.
2. Every skill's name and one-line description are rendered into a skills block in the system prompt (the agentic runtime's `<available_skills>` block also carries the absolute path of each `SKILL.md`).
3. The model judges whether the current task matches a skill's description; on a match, it reads the full `SKILL.md` with the `read` tool. The full text is never injected automatically.

A skill executes nothing by itself. Scripts, reference files, and data can sit next to `SKILL.md` and be run through the existing `bash` / `execute_code` tools. Every discovered skill is also projected into the slash-command registry, so typing `/<name>` in chat inserts its body.

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

The system prompt, management CLI, and slash-command projection use the same four sources: remote-cache (`~/.openprogram/cache/skills/`), plugin-contributed (see [Plugins](plugins.md)), user (`~/.openprogram/skills/`), and project (`<cwd>/skills/`). On a name collision, the later and more local source wins. OpenProgram ships no default skills; product workflows are Programs.

## Management commands

```bash
openprogram skills list       # list discovered skills by source (user / project / plugin / remote-cache)
openprogram skills doctor     # scan skill directories for problems
openprogram skills search <q>       # search discovery sources (ClawHub by default)
openprogram skills install <spec>   # install: slug (ClawHub by default), clawhub:<slug>, github:owner/repo
openprogram skills update --all     # compare local SKILL.md hashes with upstream, re-pull stale ones (or pass one name)
openprogram skills remove <slug>    # remove an installed skill (project / user / remote-cache only)
```

`install` accepts `--source` to name a discovery-source URL (`clawhub://`, a GitHub repo, or a JSON index).

# Slash Commands — Unified Design

Design goal: merge OpenProgram's currently scattered, mutually disconnected "command sources" — the CLI hardcoded table, the Web composer hardcoded table, `/api/plugins/commands`, MCP prompts, and skills — into a single **unified slash-command registry**: five layers, one format, one rendering pipeline, one UI.

For the reference implementations, see `docs/design/cli/slash-commands-references.md`. Wherever this document says "borrow from X," it means we directly reuse that project's design choice.

---

## 1. Source Layers

Load order is low to high, with **a higher-priority layer overriding a lower-priority one of the same name**; the overridden command is not lost and can still be invoked explicitly via `/source:name`.

| Layer | Source | Directory / Interface | Author | Hot reload |
|---|---|---|---|---|
| L0 | built-in | hardcoded in source | OpenProgram itself | no (requires restart) |
| L1 | plugins | `entrypoints.commands` under `~/.openprogram/plugins/<pkg>/...` | plugin authors | on plugin reload |
| L2 | mcp-prompts | `list_prompts()` of connected MCP servers | MCP servers | on session reconnect |
| L3 | skills | `~/.openprogram/skills/<name>/SKILL.md` | skill authors / users | via watcher |
| L4 | user | `~/.openprogram/commands/**/*.md` | current user | via watcher |
| L5 | project | `<cwd>/.openprogram/commands/**/*.md` | project maintainers | via watcher |

Override rules borrow from claude-code: the later-loaded one wins; within the same source, duplicates of the same name are deduplicated by realpath to prevent symlinks from loading the same file twice.

Explicit namespace format: `/(plugin)name`, `/(mcp:linear)name`, `/(skill)name`, `/(user)name`, `/(project)name`. The text inside the parentheses is the source label.

On conflict, the menu shows the primary entry plus a hint "this name has N other sources, press ⇥ to switch," borrowing the disambiguation UI approach from claude-code.

---

## 2. File Format

Borrow claude-code's markdown + YAML frontmatter; the field set is the union of claude-code + opencode + openclaw + hermes, then trimmed of what's meaningless to us (i18n, provider routing, platform filter).

```markdown
---
# Identity ----------------------------------------------------------
name: review              # optional; defaults to the file name
aliases: [r, rev]         # optional; also subject to override rules in the name table
description: Review the current diff per team conventions
when-to-use: |            # long description; shown in the picker detail panel
  Use when the user wants a round of style + bug review on uncommitted changes.
hidden: false             # if true, does not appear in the completion menu, but can still be triggered explicitly

# Arguments ----------------------------------------------------------
arguments:                # positional argument declarations (opencode style)
  - name: target
    description: File path or directory; defaults to the current diff
    required: false
argument-hint: "[target]"  # grayed-out hint in the menu (claude-code)

# Execution ----------------------------------------------------------
type: prompt              # prompt | local | local-jsx, default prompt
context: inline           # inline | fork, default inline
agent: general-purpose    # only takes effect when context: fork
model: inherit            # inherit | opus | sonnet | haiku | <full id>
effort: medium            # low | medium | high | max | <int>
allowed-tools:            # tool allowlist passed to the sub-agent in fork mode
  - Read
  - Grep

# Trigger conditions (claude-code only) -----------------------------
paths:                    # globs; the command only appears in completion when matched
  - "src/**/*.{ts,tsx}"
  - "**/*.py"
requires:                 # openclaw-style prerequisite checks
  any-bins: [git]
  config: [openai_api_key]

# Hooks ----------------------------------------------------------
hooks:                    # shares event names with plugins/hooks
  PreToolUse: ...

# Meta -----------------------------------------------------------
version: 1.0.0
---
Please review this code at {{target}}:
- Find potential bugs
- Check whether it conforms to CONVENTIONS.md
- Output patch suggestions

Additional context:
$ARGUMENTS

Recent commits:
!`git log -5 --oneline`

Current diff:
@`git diff --staged`
```

The authoritative field table is in Section 7.

---

## 3. Command Body Template Syntax

Borrow all of claude-code's, plus opencode's `$0..$9`, plus hermes's timeout-bounded shell.

| Syntax | Meaning | Source |
|---|---|---|
| `$ARGUMENTS` | the entire text the user typed after the command | claude-code |
| `$0`..`$9` | the Nth positional argument (shell-style tokenization) | opencode |
| `{{name}}` | a named argument declared in `arguments:` | opencode + custom |
| `${OPENPROGRAM_COMMAND_DIR}` | absolute path of the directory containing the command file | claude-code |
| `${OPENPROGRAM_SESSION_ID}` | current session id | claude-code |
| `${OPENPROGRAM_CWD}` | current working directory | new |
| `` !`cmd` `` or code block `` ```! ` ` | execute in the host shell, splice stdout back into the prompt; 2s timeout | claude-code + hermes |
| `` @`path` `` | read file contents and splice back into the prompt; the path must be inside trusted_roots | new |
| `<<command-name>>name<</command-name>>` | reference another command and expand it (recursion guard, max 3 levels) | new |

Argument parsing borrows claude-code's `tryParseShellCommand`: first tokenize with shell-quote, and on failure fall back to whitespace split. Empty arguments return an empty list, and `$0..$9` in the template resolve to empty strings.

Numeric named arguments (`name: "0"`) are rejected at registration time, as they conflict with `$0`.

Security model for shell execution: disabled by default; you must set `commands.allow_shell: true` in the config before `` !`...` `` can run. All `!` blocks are disabled in the MCP context.

---

## 4. Execution Modes

Borrow claude-code's three states, plus opencode's subtask concept.

```
type: prompt            (default)
  render the template → inject it as a user message into the current session → run the normal agent loop

type: local
  call the host-registered LocalCommandHandler; returns a LocalCommandResult
  reserved for built-in commands: /compact /clear /new /web /model etc.

type: local-jsx
  not implemented for now. The Web UI can render a React component as the command result (claude-code uses ink)
  on our side we go through a server-pushed structured event; leave the interface open

context: inline          (default)
  the rendered prompt enters the current session context

context: fork
  run in a task sub-agent (uses existing functions/tools/task)
  the agent field determines subagent_type; allowed-tools determines the visible tool set
  the sub-agent's final message is presented as a "command result" and does not pollute the main context
```

`context: fork` is equivalent to "typing `/review` automatically turns into a single call to `task(subagent_type=general-purpose, prompt=...)`." This step grafts claude-code's fork mode directly onto our existing subagent mechanism.

---

## 5. Trigger Conditions (paths / requires)

Borrow claude-code's `paths` and openclaw's `requires`: when conditions are not met, **hide from the completion menu**, but the user can still trigger it by manually typing the full command — at which point a hard validation runs again, with a clear error on failure.

`paths: ["src/**/*.py"]`: shown only when files recently touched in the current session (or explicitly referenced via `@file`) match the glob.

`requires.any-bins: [git, rg]`: a `which` check that at least one is available. On failure, hint "needs `git` / `rg`, please install first."

`requires.config: [openai_api_key]`: the current profile has this key configured.

`requires.platform: [darwin, linux]`: a platform filter borrowed from hermes.

---

## 6. Hook Binding

A command can declare its own temporary hook (effective only during that command's execution). Event names reuse `openprogram/plugins/hooks.py:HookEvent`.

```yaml
hooks:
  PreToolUse:
    - matcher: Bash
      command: !`echo "blocked by /review" >&2; exit 2`
  PostToolUse:
    - matcher: Edit
      handler: built-in:auto-stage
```

Two handler forms:

- `!` backtick block → run shell, stdout goes to the log, exit code decides allow/deny
- `built-in:<id>` → call a host-registered named handler (not in the first release; interface left open)

This is only fully usable once the hooks subsystem completes its "intercept / rewrite" semantics. This section reserves the schema first to avoid breaking changes later.

---

## 7. Authoritative Frontmatter Field Table

| Field | Type | Default | Meaning | Borrowed from |
|---|---|---|---|---|
| name | string | file name stem | command name | common |
| aliases | string[] | [] | aliases, independently subject to the override table | claude-code |
| description | string | "" | one-line description, shown in the menu | common |
| when-to-use | string \| md | "" | long description, detail panel | claude-code |
| hidden | bool | false | hide from completion | claude-code (isHidden) |
| arguments | list | [] | positional argument declarations | opencode |
| argument-hint | string | auto-generated | grayed-out menu hint | claude-code |
| type | enum | prompt | execution mode | claude-code |
| context | enum | inline | inline / fork | claude-code |
| agent | string | "general-purpose" | subagent_type when forking | claude-code |
| model | enum/string | inherit | model override | claude-code |
| effort | enum/int | inherit | reasoning effort | claude-code |
| allowed-tools | string[] | inherit | tool allowlist | claude-code |
| paths | string[] | null | glob-conditioned activation | claude-code |
| requires | object | {} | prerequisites | openclaw |
| hooks | object | {} | temporary hooks | claude-code |
| version | semver | null | for upgrade prompts | claude-code |
| user-invocable | bool | true | whether `/name` triggers it; when false, only the model can call it | claude-code |
| shell | enum | inherit | bash / powershell, used by `!` blocks | claude-code |

Any undeclared fields are preserved into an `extras` dict without error (forward compatible).

---

## 8. UI

The completion menu is grouped by source, alphabetically within a group, and across groups by source priority (project > user > skill > mcp > plugin > builtin). Each entry shows:

```
/review                  Review the current diff per team conventions      [project]
  [target]
```

Search is fuzzy (name + description + when-to-use).

Detail panel (expand with ⇥):

```
/review (project)
─────────────────────────────────────
Review the current diff per team conventions

Arguments: [target] (optional)
Mode: inline · model: inherit · effort: medium
Source: .openprogram/commands/review.md
```

Conflict state: the menu entry is tagged `(+2 more)` on the right; ⇥ switches between implementations from different sources.

Skills (L3) and MCP prompts (L2) are auto-injected; skill commands default to `context: fork`, `agent: general-purpose`, and MCP prompts default to `type: prompt + inline` (since they are prompt templates to begin with).

---

## 9. Security

- Path loading: after `realpath` resolution, the path must fall within trusted_roots (`~/.openprogram/`, `$cwd/.openprogram/`), otherwise rejected.
- YAML parsing: `yaml.safe_load`, with arbitrary-type construction disabled.
- Glob: `fnmatch` style, with `..` and absolute paths disabled.
- `!` shell blocks: disabled by default, 2s timeout, fork bombs forbidden, stdout capped at 64KB.
- `@` file references: must be inside trusted_roots or explicitly authorized via `--allow-file <abs>`.
- The source label is always written by the loader; frontmatter is not allowed to self-report `source:`.

---

## 10. Engineering Implementation

### 10.1 Directory Layout

```
openprogram/commands/
├── __init__.py             # public API: list_commands / get / dispatch
├── loader.py               # scan + parse + merge the five layers
├── frontmatter.py          # YAML parsing + field validation
├── template.py             # $ARGUMENTS / {{name}} / !`...` / @`...` rendering
├── conditions.py           # paths / requires evaluation
├── registry.py             # in-process merged table + conflict index
├── dispatch.py             # type/context branching
├── watcher.py              # inotify/fsevents watch for L3-L5
└── _ref.py                 # lightweight view projection for web/cli
```

### 10.2 Data Flow

```
startup / reload
  → loader.scan_all_layers()
  → for each layer: read files / call provider (plugins, mcp.list_prompts)
  → frontmatter.parse + validate
  → registry.merge(layer, items)        override + aliases + conflict index

user types /review xxx
  → cli or web forwards to dispatch.invoke(name, raw_args, session_ctx)
  → registry.resolve(name) → CommandSpec
  → conditions.check(spec, session_ctx) → ok / blocked-with-reason
  → template.render(spec.body, parsed_args, env)
  → dispatch by type:
       prompt + inline → session.append_user_message(rendered)
       prompt + fork   → task.run(agent=spec.agent, prompt=rendered, tools=allowed)
       local           → handler(session_ctx, parsed_args)
```

### 10.3 API

Backend:

```
GET    /api/commands                    # the merged unified list (with source, metadata)
GET    /api/commands/{name}             # single-entry detail (with body template preview)
POST   /api/commands/{name}/invoke      # body: {session_id, raw_args}
POST   /api/commands/reload             # force a rescan
GET    /api/commands/conflicts          # conflict table (same name, multiple sources)
```

`/api/plugins/commands` is retained as a compatibility entry point, internally redirecting to `/api/commands?source=plugin`.

Frontend:

`web/components/chat/composer/use-slash-menu.ts` is changed to read `/api/commands`, deleting the internal hardcoded list (keeping a dispatcher compatibility layer that maps client-side `/compact`, `/clear`, etc. to builtin local commands).

CLI:

`openprogram/_cli_chat/handlers.py:_handle_slash` is reworked: first check the registry, and on a hit go through dispatch; on a miss, fall back to the current hardcoded if/else chain (which is gradually migrated into type: local builtin commands).

### 10.4 Migration Path

```
Phase 1  scanner + frontmatter + rendering + registry + /api/commands   [foundation]
Phase 2  L4 (~/.openprogram/commands) + L5 (.openprogram/commands)
Phase 3  L3 skills auto-injection (skills/loader exposes to_command_spec())
Phase 4  L2 mcp prompts auto-injection (mcp/registry already has list_prompts)
Phase 5  L1 plugins onboarded onto the new table (plugins/loader already has _commands, add an adapter layer)
Phase 6  context: fork wired to the task tool
Phase 7  paths / requires trigger-condition evaluation
Phase 8  watcher hot reload
Phase 9  builtin commands gradually migrated to type: local + frontmatter
Phase 10 hooks field enabled after the hooks subsystem is upgraded
```

Each Phase is independently shippable; once Phase 1-2 + 5 are done, most of the user value is already delivered.

---

## 11. What We Don't Implement (Explicitly Dropped)

| Source | Design | Reason for not copying |
|---|---|---|
| openclaw | i18n (descriptionLocalizations) | we have just English + Simplified Chinese, runtime switching adds no value |
| openclaw | provider routing (Slack vs Mattermost) | single host, no multi-provider naming |
| hermes | platforms filter (darwin/linux/win32) | replaced by requires.platform |
| hermes | prompt-injection 134-pattern detection | moved to a standalone prompt-injection scanner subsystem |
| claude-code | local-jsx React components | the Web UI uses structured events instead, CLI does not implement it |
| pi-mono | pure hardcoding | a counterexample |

---

## 12. Versioning and Upgrades

The `version: 1.0.0` field is stored in the registry together with the source repo's git hash (if any).

L1 (plugins) goes through the plugin autoupdate subsystem (already exists).

L3 (skills) goes through skills discovery diff (already exists).

L4 / L5 are user-written and do not auto-update.

L0 (builtin) tracks the OpenProgram version.

`docs/design/cli/slash-commands-references.md` periodically rescans the implementations of the five reference projects; when new designs are found, return to §2/§3 of this document to add fields, without breaking existing frontmatter (extra fields go into extras).

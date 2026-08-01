# Slash Commands — Reference Implementations Snapshot

A snapshot of how five other projects implement slash commands. Each entry
records: source directories, frontmatter fields, template syntax, execution
modes, argument parsing, UI, and security.

These projects keep evolving upstream. When OpenProgram wants to sync again,
come back here, diff each entry item by item, and lift any new design into the
field table in `slash-commands.md`.

Reference checkout path:
`/Users/fzkuji/Documents/LLM Agent Harness/OpenProgram/references/`

---

## 1. claude-code-leaked

**Location**: `references/claude-code-leaked/src/skills/`,
`src/utils/argumentSubstitution.ts`, `src/types/command.ts`

### Sources / load order

`loadSkillsDir.ts:638-714`:

```
managed (policy-delivered)
  ↓ later overrides earlier
user      ~/.claude/skills/
  ↓
project   .claude/skills/  (recursed upward from cwd)
  ↓
legacy commands
```

- Loaded in parallel.
- `realpath` deduplication so a symlink cannot load the same file twice.
- `conditionalSkills: Map<sessionId, Skill[]>` holds conditionally activated
  entries (the `paths` field).
- lodash memoize caching, cleared by `clearSkillCaches()`; **no watch**.

### Frontmatter fields (`src/utils/frontmatterParser.ts:10-59`)

| Field | Type | Default |
|---|---|---|
| allowed-tools | string[] | none |
| description | string | "" |
| argument-hint | string | "" |
| when-to-use | string | "" |
| version | semver | null |
| hide-from-slash-command-tool | "true"\|"false" | false |
| model | "haiku"\|"opus"\|"inherit" | inherit |
| skills | string (comma-separated) | "" |
| user-invocable | "true"\|"false" | true under commands/, false under skills/ |
| hooks | HooksSettings (PreToolUse etc.) | {} |
| effort | "low"\|"medium"\|"high"\|"max"\|int | inherit |
| context | "inline"\|"fork" | inline |
| agent | string | "general-purpose" |
| paths | string \| string[] | null |
| shell | "bash"\|"powershell" | inherit |

### Command body syntax (`loadSkillsDir.ts:344-399`, `argumentSubstitution.ts`)

- `$ARGUMENTS`
- `$0`-`$9` (positional)
- `$name` (named arguments)
- `${CLAUDE_SKILL_DIR}`
- `${CLAUDE_SESSION_ID}`
- `` !`cmd` `` runs a shell command; skipped in the MCP context
- `` ```! ` ` code block, same as `` !`...` ``
- **No** `@file` references and no calling other commands

### Argument parsing (`argumentSubstitution.ts:24-68`)

- `tryParseShellCommand` first (supports quoting and escapes).
- On failure, falls back to whitespace split.
- Empty arguments return `[]`; `$0..$9` in the template become empty strings.
- Numeric named arguments are rejected (they conflict with `$0`).

### Execution modes (`src/types/command.ts:25-57`)

```
type: prompt        → ContentBlockParam, generated dynamically by getPromptForCommand
type: local         → Promise<LocalCommandResult>; supports skip (shows no message)
type: local-jsx     → React (ink) component with an onDone callback
context: inline     → expanded into the current session
context: fork       → runs in a sub-agent (the agent field sets subagent_type)
```

### MCP integration (`src/skills/mcpSkillBuilders.ts`)

MCP tools automatically become slash commands with source `"mcp"`, named
`mcp:tool-name` and displayed as `/mcp:tool-name (MCP) ...`.

### Conflicts

The later load wins (managed < user < project). Files with the same realpath
are deduplicated, keeping the first.

### Security

- `realpath` plus a trusted_roots allowlist.
- YAML special-character preprocessing: glob patterns are auto-quoted
  (`quoteProblematicValues`).
- Shell selection is per file (it does not read `settings.defaultShell`).

### Distinctive

- `paths` conditional activation
- `context: fork` sub-agents
- `effort` reasoning strength
- `hooks` embedded in the command file
- `hide-from-slash-command-tool` (hidden)
- automatic MCP slash exposure

---

## 2. opencode

**Location**: `references/opencode/packages/opencode/src/config/command.ts`

### Sources

```
Glob: {command,commands}/**/*.md  (absolute paths)
```

Effect Schema validates the frontmatter and merges it with the content. No
deduplication, no watch, no conditional activation.

### Frontmatter (minimal, four fields)

| Field | Type |
|---|---|
| template | string (required) |
| description | string |
| agent | string |
| model | ConfigModelID |
| subtask | boolean |

### Templates

Plain text expansion; `$ARGUMENTS` plus `$0-9` positional arguments; the
frontmatter `arguments:` field declares the list.

### Distinctive

- The strongest type safety of the five (Effect).
- `subtask: true` — a simplified version of claude-code's fork.
- MCP skills return a lazy Promise.

---

## 3. openclaw

**Location**: `references/openclaw/src/auto-reply/commands-registry.*`

### Sources

A data-driven hardcoded registry with **no filesystem scan**, plus
provider-specific mapping (Slack vs Mattermost).

### Fields (`commands-registry.types.ts`)

```
ChatCommandDefinition:
  key | string               # internal ID
  nativeName | string        # Slack/Mattermost name
  nativeAliases? | string[]
  description | string
  descriptionLocalizations? | Map<lang, desc>
  scope | "text" | "native"
  args? | CommandArgDefinition[]
  acceptsArgs | boolean
```

### Distinctive

- Multi-provider routing (`resolveNativeName`, the pluginProvider hook).
- i18n (descriptionLocalizations).
- Argument choice menus (CommandArgChoiceContext), similar to a Discord slash
  choice dropdown.

### SKILL system

`SKILL.md` plus frontmatter carrying `emoji`, `requires.anyBins`,
`requires.config`, and `install[]` (dependency checks plus install guidance).

---

## 4. hermes-agent

**Location**: `references/hermes-agent/tools/skills_tool.py`,
`agent/skill_commands.py`, `agent/skill_preprocessing.py`

### Sources

```
~/.hermes/skills/   a single directory
  ↓ bundled / hub-installed / edited entries coexist
get_external_skills_dirs()   extension point
```

trusted_roots validation with path-traversal protection.

### Frontmatter

- `metadata.hermes.config`: configuration variable declarations.
- `platforms`: ["darwin", "linux", "win32"].
- `metadata.hermes.*` takes priority over top-level fields.

### Command body (`skill_preprocessing.py`)

- `_substitute_template_vars`
- `_expand_inline_shell` (with a timeout)
- Skill config injection: a `[Skill config: ...]` block

### Distinctive

- Platform filtering.
- 134 prompt-injection pattern checks.
- A secret-capture callback (`_secret_capture_callback`).
- Single-directory design.

---

## 5. pi-mono

**Location**: `references/pi-ai/packages/coding-agent/src/core/slash-commands.ts`

### Sources

A hardcoded builtin list (settings / model / export / import / fork / clone)
with **no filesystem scan for extensions**.

### Distinctive

The `SlashCommandSource` enum has extension | prompt | skill, but only builtin
is actually used.

It serves as the counterexample — the alternative trade-off of having no
extension mechanism at all.

---

## 6. Feature matrix

| Feature | claude-code | opencode | openclaw | hermes | pi-mono |
|---|---|---|---|---|---|
| filesystem scan | recursive + symlink-safe | glob | none | single dir | none |
| frontmatter field count | 15+ | 5 | n/a | ~6 | 0 |
| `$ARGUMENTS` | ✓ | ✓ | ✗ | ✓ | ✗ |
| `$0..$9` | ✓ | ✓ | ✗ | ✗ | ✗ |
| named args `$name` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `${ENV_VAR}` | ✓ | ✗ | ✗ | ✓ | ✗ |
| `` !`shell` `` | ✓ | ✗ | ✗ | ✓ (timeout) | ✗ |
| `@file` references | ✗ | ✗ | ✗ | ✗ | ✗ |
| conditional activation via paths | ✓ | ✗ | ✗ | ✗ | ✗ |
| context: fork | ✓ | ~subtask | ✗ | ✗ | ✗ |
| effort reasoning strength | ✓ | ✗ | ✗ | ✗ | ✗ |
| MCP auto-slash | ✓ | ✗ | ✗ | ✗ | ✗ |
| embedded hooks | ✓ | ✗ | ✗ | ✗ | ✗ |
| multi-directory override | ✓ | ✗ | ✗ | ✗ | ✗ |
| realpath deduplication | ✓ | ✗ | ✗ | ✗ | ✗ |
| i18n | ✗ | ✗ | ✓ | ✗ | ✗ |
| provider routing | ✗ | ✗ | ✓ | ✗ | ✗ |
| platform filter | via requires (custom) | ✗ | ✗ | ✓ | ✗ |
| dependency check | ✗ | ✗ | ✓ (requires/install) | ✗ | ✗ |
| hot reload | ✗ | ✗ | ✗ | ✗ | ✗ |

**Common to all five**: a description field.

**Common to three or more**: name + description + body template, frontmatter
parsing, and a distinction between execution modes.

**Distinctive and worth borrowing**:

```
claude-code  →  context:fork + paths + hooks + effort + realpath dedup + MCP auto-slash
opencode     →  positional args $0-9 + explicit arguments[] declarations
openclaw     →  requires.anyBins + install hints + i18n descriptions
hermes       →  inline shell with a timeout + platform filter
pi-mono      →  counterexample only
```

**OpenProgram's choices**: take claude-code's design wholesale, merge in
opencode's `$0-9` and `arguments[]`, keep openclaw's `requires` as its own
field, apply hermes's timeout idea to `!` shell blocks, and use pi-mono's
hardcoded-builtin approach only for `type: local` builtin commands.

---

## 7. Sync procedure

Run periodically (for example each quarter):

1. `cd references && git pull` to update all reference projects.
2. Diff each project's field table above against upstream:
   - new fields → decide whether they belong in the authoritative field table
     in `slash-commands.md` §7
   - new template syntax → decide whether it belongs in §3
   - new execution modes → decide whether it belongs in §4
   - new security constraints → §9
3. Update this file with what changed.

If upstream makes a breaking change (for example claude-code renaming a
frontmatter field), **do not follow it**. OpenProgram's frontmatter is a
stable contract and backward compatibility comes first; put the alias mapping
in `_ALIAS_MAP` in `frontmatter.py` so both the old and new spellings parse.

# Permission System Design

This is the **implementation-level design document** for the OpenProgram permission system: after reading it, someone unfamiliar with the code knows what the permission system is, which permissions exist, how the backend is written, how the frontend is written, and which files hold the code. Every data structure gets its field definitions, every key function gets its signature, every WS frame gets its fields, every frontend component gets its structure. All references carry `file:line`.

Reading order: **① Overview** (what it is, how it runs) → **② Which permissions exist** (definitions of modes and rules) → **③ Backend implementation** (decisions, matching, storage) → **④ Frontend implementation** (approval card, mode picker, rule management) → **⑤ Attended mode** (an orthogonal mechanism) → **⑥ Key constraints and code map** → **⑦ Explicit non-goals**.

---

## 1. Overview

### 1.1 What the permission system solves

When the model wants to call a tool (bash / write / …), there are only three possible dispositions: **execute directly**, **ask the user first**, or **deny outright**. The permission system is the mechanism that decides which path each tool call takes. It is not a security sandbox (it does no process or file isolation); it is a **decision and awareness layer** — it lets the user control "what runs automatically, what needs a nod, what is never allowed", and shows clearly what is being approved when a nod is needed.

### 1.2 How the four parts cooperate

Permission decisions run through four parts chained into a decision path, from hard to soft:

| Part | Governs | Can `bypass` disable it | Location |
|---|---|---|---|
| **gate (hard block)** | Absolute prohibitions from the policy layer (proactive policy deny/ask) | No, always in effect | `openprogram/events/tool_gate.py` |
| **Rule layer** | User-configured allow / deny / ask rules (per-tool + per-pattern, **project level** primarily, layered across sources) | No for deny/ask; yes for allow | `openprogram/agent/internals/_approval.py:50-68` (`_match_rule`) + `openprogram/programs/permission_rule.py` |
| **Permission mode (session level)** | Session tier: ask / acceptEdits / auto / bypass / plan (aligned with Claude Code's official names, 5 tiers) | The tier itself is that switch | `_gated_execute` (`internals/_approval.py:150-197`) |
| **Approval flow** | Frontend/backend interaction when a nod is needed (show card, block waiting for an answer, write back project rules) | No (showing the card blocks) | `await_user_approval` (`internals/_approval.py:245-`) + frontend approval mode |

One safety constraint runs through the whole document: **decision priority is deny > ask > allow, and the deny/ask decision happens before the bypass short circuit.** Because the web entry point defaults to bypass (`webui/_execute/__init__.py:552-553`), placing deny rule matching after bypass would silently ignore a user's "forbid rm -rf" under the default — that is a security defect. The decision pseudocode in section 3 strictly guarantees the ordering.

### 1.3 Data flow

```
LLM issues a tool call
        │
        ▼
┌──────────────────────────────────────────────────────────────┐
│ agent_loop.py:695  build the tool.before event                 │
│ agent_loop.py:701  decide_tool_gate(before_ev)  ← gate block    │
│   deny → raise ToolGateDenied → error tool result to model      │
└──────────────────────────────────────────────────────────────┘
        │ allowed through
        ▼
┌──────────────────────────────────────────────────────────────┐
│ _gated_execute (internals/_approval.py:150-197)                │
│                                                                │
│  ① Rule layer deny/ask (before bypass)                         │
│     _match_rule → "deny" → return [denied] (any mode, incl.    │
│       bypass)                                                  │
│     _match_rule → "ask"  → force await_user_approval (incl.    │
│       bypass)                                                  │
│  ② force_ask tools (exit_plan_mode) → forced approval          │
│  ③ permission_mode == "bypass" → execute directly              │
│  ④ Rule layer allow (after bypass)                             │
│     _match_rule → "allow" → execute directly                   │
│  ⑤ Read-only safe tools (SAFE_AUTO_ALLOWLIST) → execute in all │
│     modes                                                      │
│  ⑥ permission_mode == "acceptEdits" and the tool is write-safe │
│     → execute directly                                         │
│  ⑦ permission_mode == "auto" → risky tools return [denied];    │
│     everything else goes to the haiku classifier               │
│  ⑧ Everything else → await_user_approval, card blocks          │
└──────────────────────────────────────────────────────────────┘
        │
        ▼   when approval is needed
┌──────────────────────────────────────────────────────────────┐
│ await_user_approval → open_question(kind="approval")           │
│   → emit_question_asked → event layer → WS question.asked      │
│   → frontend approval mode renders the approval card           │
│   → user clicks Allow once / Always allow / Deny               │
│   → question_reply/question_reject → _resolve_question          │
│   → threading.Event wakes → consume_or_timeout → (approved,    │
│      reason, scope)                                            │
│   scope=="always" → _persist_always_allow_rule → write back to │
│      project rules                                             │
└──────────────────────────────────────────────────────────────┘
        │
        ▼
   approved → orig_execute   |   denied/timeout → [denied] error result
```

---

## 2. Which permissions exist (definitions)

This section covers only the "what" — the definitions of permission modes and rules. Implementation lives in sections 3 and 4.

### 2.1 Permission modes (5 tiers, Claude Code's official names)

The permission mode is **session level**: it lives in `SessionRunConfig.permission_mode`, the frontend picks it in the permission badge on the chat page top bar (§4.5), and it is isolated per session. It is defined at `openprogram/agent/dispatcher/types.py:19`, with the legal value set at `openprogram/agent/session_config.py:27` (aligned with Claude Code's official 5 tiers):

```python
# openprogram/agent/dispatcher/types.py:19
PermissionMode = Literal["ask", "acceptEdits", "plan", "auto", "bypass"]

# openprogram/agent/session_config.py:27
VALID_PERMISSION = {"ask", "acceptEdits", "plan", "auto", "bypass"}
_PERMISSION_BY_LOWER = {m.lower(): m for m in VALID_PERMISSION}  # case-insensitive normalization
```

Internal tier values use the official English names, and frontend labels follow Claude Code's official labels (`MODE_LABELS` in `use-permission-mode.ts`):

| Mode (internal value) | Frontend label | Behavior |
|---|---|---|
| `ask` | Ask permissions | Every tool call shows an approval card and blocks waiting for an answer (unless a rule allows it, it is a read-only safe tool, or the per-tool declaration says no approval is needed). One prompt per call. |
| `acceptEdits` | Accept edits | Tools that are **write-class and path-safe** (read/write/edit/glob/grep/list, targeting a path inside the working directories and not a dangerous file) are auto-allowed; command-class tools such as bash/exec/shell **still go through full approval**. |
| `auto` | Auto mode | The LLM classifier tier. Risky tools (`RISKY_AUTO_DENYLIST`: bash/exec/shell/execute_code/process) return `[denied]` outright; anything else that is uncertain gets one haiku call to judge safety (`internals/_auto_classifier.py`). |
| `bypass` | Bypass permissions | Everything is allowed through, no approval card; execution runs under the sandbox's `escalated_policy` (configurable limits off, hard floor on) unless `sandbox.apply_in_bypass=true` — see [sandbox.md](sandbox.md). **Exception**: `exit_plan_mode` forces approval (`_FORCE_APPROVAL_TOOLS`, `internals/_approval.py:34`); rule-layer deny/ask still applies. |
| `plan` | Plan mode | Planning state. Write-class tools are invisible to the model in this mode (`apply_tool_policy(source="plan")`) — pure **visibility** control, orthogonal to approval strength (see §3.7). |

> Case normalization: `acceptEdits` is camel case. `VALID_PERMISSION` stores the camel-case canonical values, and `_PERMISSION_BY_LOWER` builds a `lowercase → canonical` table; `_normalize_permission` (`session_config.py:289-293`) uses `_PERMISSION_BY_LOWER.get(value.lower())` for case-insensitive matching, so a frontend sending `"acceptedits"` still normalizes back to `"acceptEdits"`, and illegal values return `None`.

### 2.2 Rules (allow / deny / ask, three parallel lists)

Rules are user-configured overrides, orthogonal to the tier, and **carried primarily by the project** (see §2.3). Three parallel lists; a rule's behavior comes from which list it lives in, not from a field inside the string:

```python
# openprogram/agent/session_config.py:35-45
@dataclass
class PermissionRules:
    allow: list[str] = field(default_factory=list)
    deny:  list[str] = field(default_factory=list)
    ask:   list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.allow or self.deny or self.ask)
```

**Rule string syntax**:

```
ToolName                 Whole tool, per-tool. e.g. Bash / write_file / read_file
ToolName(content)        Command level, per-pattern. e.g. bash(git:*) / read_file(/etc/**)
```

- `bash` → `{tool_name="bash"}` (the whole bash tool)
- `bash(git status)` → `{tool_name="bash", pattern="git status"}` (exact command)
- `bash(git:*)` → `{tool_name="bash", pattern="git:*"}` (prefix wildcard: commands starting with `git `)
- `read_file(/etc/**)` → `{tool_name="read_file", pattern="/etc/**"}` (path glob)
- Escaping: `(`, `)`, and `\` inside a pattern must be escaped (`\( \) \\`) because they are syntax delimiters. Serialization and deserialization are duals.

```python
# openprogram/programs/permission_rule.py:19-22
@dataclass(frozen=True)
class PermissionRuleValue:
    tool_name: str
    pattern: str | None = None   # None = per-tool; non-None = per-pattern
```

### 2.3 Rule sources (3 layers, the project is the primary carrier)

Rules can come from multiple sources, lowest priority first and highest last, with later entries overriding earlier ones. Merging happens in `load_merged_rules(session_id)` (`openprogram/programs/permission_rule.py:100-146`). **The project layer is the primary carrier of rules** — rules follow the project, survive session switches, and let "always allow" be remembered long term. Only carriers that actually exist are mapped (no local/cliArg/enterprise policy backend, see the non-goals in section 7):

| Layer | Priority | Carrier | Writable |
|---|---|---|---|
| global (global config) | Lowest | `tools.permission_rules` in the global config (`webui._setup._read_config()`) | Yes |
| **project (primary carrier)** | ↑ | `permission_rules` in `<project>/.openprogram/settings.json`; the default project lands in `<state>/projects/default-settings.json`. Resolved via `project_for_session(session_id)` | Yes |
| session (this session, one-off override) | Highest | `SessionRunConfig.permission_rules`, persisted with the session into session meta (schemaless) | Yes |

- Reading and writing the project layer: `load_project_settings` / `save_project_settings` in `openprogram/store/project/project_store.py` (`:565-`); the carrier path is decided by `_settings_path_for` (`:559-`) — non-default projects land in `<project>/.openprogram/settings.json`, the default project lands in `<state>/projects/default-settings.json` (never write config into the home directory).
- Merging is just concatenation of the three lists: the overall ordering of deny/ask/allow is guaranteed by `_match_rule` (first hit returns, deny > ask > allow), and source order only affects ordering within the same behavior.
- "Always allow" (scope=`always`) writes back to the **project** settings (`_persist_always_allow_rule`, `internals/_approval.py:90-106`), no longer to session meta.

### 2.4 PendingQuestion, the data carrier of the approval frame

Approvals merge into the unified `QuestionRegistry` — an approval is simply a question with `kind="approval"`, taking the same path and the same frontend landing point as `runtime.ask`.

```python
# openprogram/agent/questions.py:34-54
@dataclass
class PendingQuestion:
    id: str                    # UUID hex[:12]
    session_id: str            # webui session id, may be empty
    kind: str                  # "ask"|"confirm"|"approval"|"form"|"ask_many"
    prompt: str
    options: list[str] = field(default_factory=list)
    multi: bool = False
    allow_custom: bool = True
    detail: str = ""           # approval use: tool name + argument summary
    schema: dict = field(default_factory=dict)
    questions: list = field(default_factory=list)
    created_at: float = 0.0
    expires_at: float = 0.0
```

`_Resolution = tuple[str, object]`, and the registry's `outcome` has only two states, `{"answered", "declined"}` (`questions.py:57-58`). `"timeout"` is not a registry state; it is a value **synthesized** by `consume_or_timeout` (`questions.py:256-`) when no result arrives: `return res if res is not None else ("timeout", None)`.

---

## 3. Backend implementation

A tool call passes through two checkpoints: the gate (in the agent main loop) and the approval wrapper (inside the tool coroutine). Rule matching, per-mode branches, storage, and danger detection all live in this layer.

### 3.1 gate (checkpoint A, synchronous hard block)

In `agent_loop.py`, before each tool execution: `agent_loop.py:695` builds the `tool.before` event → `agent_loop.py:701` calls `decide_tool_gate(before_ev)` to poll every registered gate → on a deny, `agent_loop.py:708` raises `ToolGateDenied`, and the deny reason goes back to the model as an error tool result.

```python
# openprogram/events/tool_gate.py
ToolGate = Callable[[Event], "str | None"]   # returns None to allow / a string deny reason

def decide_tool_gate(event: Event) -> str | None:
    """Poll every gate and take the strictest: any deny blocks (reasons joined by "; ").
    A gate that raises → fail-open (printed to stderr), continue to the next one."""
```

Key property: **the gate sits outside the permission approval wrapper, and `bypass` cannot disable it** (`events/tool_gate.py:15`); it applies to subagents just the same. The gate is the hard block point for the policy layer (proactive policy Gate allow/deny/ask), so it must be fast (synchronous hot path, no LLM, no slow IO).

### 3.2 Approval wrapper (checkpoint B) and decision pseudocode

Tools get individually wrapped with an approval layer when they enter the dispatcher (`dispatcher/__init__.py:802`: `tools = [_wrap_with_approval(t, req, on_event) for t in tools]`; the real function name is `wrap_with_approval`, aliased at the dispatcher import). The wrapper sits **inside** the tool coroutine, because agent_loop eagerly schedules `tool.execute` and intercepting from outside races (`internals/_approval.py:121-126`). `_gated_execute` is the execute that gets substituted in (`internals/_approval.py:150-197`), with the full decision order (8 branches):

```python
# openprogram/agent/internals/_approval.py:34, 150-197
_FORCE_APPROVAL_TOOLS = {"exit_plan_mode"}  # :34

async def _gated_execute(call_id, args, cancel, on_update):
    mode = req.permission_mode
    force_ask = name in _FORCE_APPROVAL_TOOLS

    # (1) Rule layer deny/ask -- before bypass, highest safety priority
    verdict = _match_rule(getattr(req, "permission_rules", None), name, args)  # 3.4
    if verdict == "deny":
        return _denied(f"[denied] blocked by deny rule: {name}")
    if verdict == "ask":
        return await _approve_then_run(call_id, args, cancel, on_update)  # shows even under bypass

    # (2) force_ask (exit_plan_mode), bypass cannot skip it either
    if force_ask:
        return await _approve_then_run(call_id, args, cancel, on_update)

    # (3) bypass short circuit (after deny/ask/force); full access by default,
    #     sandbox.apply_in_bypass=true keeps the configured sandbox
    if mode == "bypass":
        if apply_in_bypass():
            return await orig_execute(call_id, args, cancel, on_update)
        with escalated_policy():
            return await orig_execute(call_id, args, cancel, on_update)

    # (4) Rule layer allow -- after bypass
    if verdict == "allow":
        return await orig_execute(call_id, args, cancel, on_update)

    # (5) Read-only safe tools allowed in every mode (read/grep/glob under
    #     ask / acceptEdits / plan raise no card; reuses the auto classifier allowlist)
    if name in SAFE_AUTO_ALLOWLIST:
        return await orig_execute(call_id, args, cancel, on_update)

    # (6) acceptEdits: write-safe tools auto-allowed; command-class tools fall to approval
    if mode == "acceptEdits" and getattr(agent_tool, "_accept_edits_safe", False) \
            and _path_is_safe(name, args, req):        # 3.3 / 3.5
        return await orig_execute(call_id, args, cancel, on_update)

    # (7) auto: obvious danger denied outright, uncertainty gets one haiku call
    if mode == "auto":
        if name in RISKY_AUTO_DENYLIST:
            return _denied(f"[denied] auto mode: risky tool blocked: {name}")
        should_block, reason = await auto_classify_tool(name, args)
        if should_block:
            return _denied(f"[denied] auto classifier: {reason}")
        return await orig_execute(call_id, args, cancel, on_update)

    # (8) Show the card and block for an answer (ask / plan / acceptEdits command-class land here)
    return await _approve_then_run(call_id, args, cancel, on_update)

# internals/_approval.py:139-148
async def _approve_then_run(call_id, args, cancel, on_update):
    approved, reason, scope = await await_user_approval(
        req=req, tool_name=name, args=args, on_event=on_event)
    if not approved:
        return _denied(reason_or_default(reason, name))
    if scope == "always":
        _persist_always_allow_rule(req.session_id, name)  # write back project rules, see 4.4
    return await orig_execute(call_id, args, cancel, on_update)
```

**The safety constraint that deny precedes bypass (the single most critical property of this design, keep it)**: deny/ask rule matching (① ②) must come before the bypass short circuit (③). Counterexample: if the whole rule block were inserted after bypass, then under the web default of bypass (`_execute/__init__.py:552-553`) a user's `deny: ["bash(rm -rf:*)"]` would never be consulted — rm -rf would run silently. So deny/ask is checked before bypass and allow after it. This ordering is a safety property; do not scramble it when changing `_gated_execute`.

### 3.3 Per-mode branch implementation

Numbering matches the pseudocode in 3.2:

- **acceptEdits (⑥)**: three parts — ① `@function` takes an `accept_edits_safe: bool = False` parameter (`functions/_runtime.py:767`) that lands on the tool object as `_accept_edits_safe` (`:1079`); read/write/edit/glob/grep/list each mark `True` in their `@function` (e.g. `programs/tools/write/write.py:24`, `edit/edit.py:25`, `read/read.py:28`, `grep/grep.py:101`, `list/list.py:30`, `glob/glob.py:43`), while bash/exec/execute_code do not (default `False`); ② `_path_is_safe` (`internals/_approval.py:72-87`) reuses `check_path_safety` from 3.5 (path inside the working directory set, not a dangerous file/directory, no Windows bypass); ③ command-class tools fall through to ⑧ forced approval even when a broad allow exists.
- **plan (visibility control)**: `apply_tool_policy(tools, source="plan")` (`dispatcher/__init__.py:798`) filters out write-class tools so they never enter the model's tool list. Plan state lives in a boolean set (`_active` in `agent/plan_mode.py`) and **does not switch approval strength** — it is orthogonal to the approval tier (details in §3.7). `_gated_execute` has no plan-specific branch (write-class tools are already filtered, read-only tools follow the current tier as usual).
- **auto (⑦)**: the LLM classifier tier, with three levels of filtering to save calls (`internals/_auto_classifier.py`): obviously safe read-only tools already passed at ⑤; `RISKY_AUTO_DENYLIST` (bash/exec/shell/execute_code/process) returns `[denied]` outright; anything else uncertain gets one `auto_classify_tool` call to haiku. Rule-layer deny/ask (①) still applies ahead of it, and allow (④) is unaffected.
- **ask**: every tool that misses allow, is not on the read-only allowlist, and is not per-tool exempt lands at ⑧.
- **bypass (③)**: everything after deny/ask/force executes without approval, under `escalated_policy` by default (`sandbox.apply_in_bypass=true` keeps the configured sandbox; see [sandbox.md](sandbox.md)).

### 3.4 Rule matching in `_match_rule`

```python
# openprogram/agent/internals/_approval.py:50-68
def _match_rule(rules, tool_name: str, args: dict) -> "str | None":
    """Returns "deny" | "ask" | "allow" | None (no match).
    Priority is fixed at deny > ask > allow: scan deny first and return on a hit, then ask, then allow.
    Within each tier: try per-tool first (rule.pattern is None and tool_name equal),
    then per-pattern (rule.pattern prefix/glob matched against parse_command(tool_name, args))."""
    if rules is None:
        return None
    from openprogram.programs.permission_rule import parse_rule, parse_command, pattern_matches
    cmd = None  # lazy: parse the command only when a per-pattern rule is reached
    for behavior, ruleset in (("deny", rules.deny), ("ask", rules.ask), ("allow", rules.allow)):
        for raw in ruleset:
            rv = parse_rule(raw)
            if rv.tool_name != tool_name:
                continue
            if rv.pattern is None:                      # per-tool
                return behavior
            if cmd is None:
                cmd = parse_command(tool_name, args)    # see below
            if cmd is not None and pattern_matches(
                rv.pattern, cmd, allow=(behavior == "allow"),
            ):
                return behavior
    return None
```

- **per-tool** (`rv.pattern is None`): `rv.tool_name == tool_name` matches the whole tool. For example `deny: ["bash"]` blocks all bash.
- **per-pattern** (`rv.pattern` non-empty): first derive the comparable command string `cmd = parse_command(...)`, then apply `pattern_matches` (`permission_rule.py`): a trailing `:*` → prefix match (`git:*` matches `git status`, not `github`); containing a glob character (`*?[`) → `fnmatch` (`/etc/**` matches `/etc/passwd`); otherwise exact equality. Prefix matching strips leading assignments and `env` wrapping; allow matching refuses that strip when the dropped assignments include resolution-affecting variables (`PATH`, `LD_PRELOAD`, `DYLD_INSERT_LIBRARIES`, …). Deny matching still strips them so a hijacked command cannot evade a deny rule.

Command parser and rule parsing (`openprogram/programs/permission_rule.py`):

```python
# permission_rule.py:43, 77, 83-98
def parse_rule(s: str) -> PermissionRuleValue: ...        # "bash(git:*)" -> (bash, "git:*")
def rule_to_string(v: PermissionRuleValue) -> str: ...    # dual of parse_rule
def parse_command(tool_name: str, args: dict) -> str | None:
    """Reduce tool arguments to a comparable string (used by per-pattern matching).
    bash/exec/shell/execute_code/process -> args["command"];
    read*/write*/edit*/apply_patch/list -> args["path"] or args["file_path"];
    anything else with no comparable field -> None (per-pattern does not apply,
    only per-tool can block it)."""
```

Merging rules across layers (`load_merged_rules(session_id)`, `permission_rule.py:100-146`) — concatenates the three lists in priority order global < project < session, for `_gated_execute` to use (at actual decision time `req.permission_rules` is filled in when the TurnRequest is built):

```python
# openprogram/programs/permission_rule.py:100-146
def load_merged_rules(session_id: str) -> PermissionRules:
    """Merge the three real carriers: global config < project (primary carrier) < session (one-off override).
    The project layer is resolved through project_for_session(session_id) -> load_project_settings.
    Merging is just concatenation of the three lists; the overall ordering of deny/ask/allow is
    guaranteed by _match_rule (first hit returns), and source order only affects ordering
    within the same behavior."""
```

**Relationship with per-tool `requires_approval`**: the two layers coexist and complement each other. `@function(requires_approval=...)` (`functions/_runtime.py`) is a hard-coded declaration by the tool author (`True`/`False`/`None`/`callable(**args)->bool|str`), which the dispatcher reads via `tool_requires_approval` (`functions/_runtime.py:1099`). The rule layer is the user's runtime override and runs ahead of per-tool (① ⑤ come before ⑦).

### 3.5 Danger detection and path safety

**RiskLevel + card highlighting** (`internals/_approval.py:218-242`):

```python
# openprogram/agent/internals/_approval.py:218-230
def _risk_level(tool_name: str, args: dict) -> str:
    """Danger grading for the approval card, "low"|"medium"|"high", driving frontend highlighting.
    high: command-class tools (_RISKY_TOOLS) whose command contains rm -rf / sudo / mkfs /
          fork bomb / pipe to shell / curl / wget.
    medium: other command-class tools; write/edit/delete tools. low: read-only tools."""
```

`_approval_detail` (`internals/_approval.py:232-242`, producing "tool name + full arguments, head and tail truncated when too long") gives the approval card a readable summary (the first version does not highlight dangerous tokens). The `question.asked` frame from `_on_asked` (inside `await_user_approval`) carries `tool`/`args`/`risk_level`, which the frontend uses for coloring (§4.2).

**Path safety** (`openprogram/programs/tools/files/file_safety.py`):

```python
# file_safety.py:20-40, 63
DANGEROUS_FILES = {".bashrc", ".bash_profile", ".bash_login", ".profile",
                   ".zshrc", ".zprofile", ".zshenv", ".gitconfig", ".gitmodules",
                   ".git-credentials", ".npmrc", ".pypirc", ".netrc",
                   ".mcp.json", ".claude.json", ".env"}
DANGEROUS_DIRECTORIES = {".git", ".hg", ".svn", ".vscode", ".idea",
                         ".openprogram", ".claude", ".ssh", ".gnupg"}
DANGEROUS_BASH_PATTERNS = {"python","python3","node","deno","bun","ruby","perl",
                           "php","sh","bash","zsh","eval","exec","source",
                           "sudo","ssh","npx"}

def check_path_safety(path: str, working_dirs=None) -> dict:
    """Returns {"safe": bool, "message": str}. Unsafe when: it hits DANGEROUS_FILES (by
    basename) / a segment hits DANGEROUS_DIRECTORIES / the target is outside working_dirs /
    a Windows bypass (NTFS stream ::$DATA, 8.3 short name ~1, UNC \\, trailing dots or spaces,
    DOS device names CON/PRN, triple dots .../). working_dirs defaults to [cwd]."""
```

`check_path_safety` is currently consumed only by `_path_is_safe` in the acceptEdits branch (`internals/_approval.py:72-87`): an unsafe path means acceptEdits does not auto-allow and falls through to approval at ⑦.

**Additional working directories**: `SessionRunConfig.additional_working_dirs` (§3.6) extends the working directory set used by path safety. `_path_is_safe` assembles `work_dirs = [current_worktree_path() or os.getcwd(), *req.additional_working_dirs]` (`:85-86`) and passes it to `check_path_safety` — the fence baseline shares its source with the cwd in the system prompt (the dispatcher binds the real worktree/project path into `current_worktree_path` each turn, and the process `getcwd` is only a fallback). The field flows down from session meta through `TurnRequest.additional_working_dirs` (`dispatcher/types.py:112`), populated at `webui/_execute/chat.py:259` and `channels/_conversation.py:243`. Users can add "this directory counts as safe too"; without it, only cwd is recognized.

**Capability not enabled**: `is_dangerous_allow_rule(tool_name, pattern)` (`file_safety.py:94-100`, using `DANGEROUS_BASH_PATTERNS` to judge whether an allow rule would let a dangerous command through under acceptEdits) is implemented but has no callers — "temporarily strip dangerous allow rules when entering acceptEdits" is not enabled. The system also provides no bypass-immune safetyCheck forced approval (`tool_requires_approval` is a `(bool, reason)` pair, without `classifier_approvable`): path safety only takes effect in the acceptEdits branch, and writing a dangerous file under bypass is not forcibly blocked. See the end of §7 for the follow-up plan.

### 3.6 Storage: schemaless session meta + SessionRunConfig

Storage is split in two, each owning half: **the permission mode lives in the session (session meta), the permission rules live in the project (settings.json)**.

**The session layer (mode) is schemaless** — that is why persisting the permission mode needs no DB migration. `SessionDB.update_session(session_id, **fields)` (`store/session/session_store.py:651-`) routes `head_id` specially to `idx.set_head()`, and every other field (`permission_mode` / `additional_working_dirs`, plus `permission_rules` for one-off overrides) goes through `idx.set_meta(**clean)` into session meta. So adding a session-level permission field only changes load/save in `session_config.py`, and old sessions still read back without error.

```python
# openprogram/store/session/session_store.py:651-
def update_session(self, session_id, **fields):
    """head_id -> set_head(); every other field -> set_meta(**clean). Schemaless."""
```

```python
# openprogram/agent/session_config.py:47-61
@dataclass
class SessionRunConfig:
    tools_enabled: Optional[bool] = None
    tools_override: ToolsOverride = None
    web_search: Optional[bool] = None
    toolset: Optional[str] = None
    thinking_effort: Optional[str] = None
    permission_mode: Optional[str] = None
    # -- Permission rules (the session layer is the highest-priority one-off override;
    #    the primary carrier is the project, see 2.3) --
    permission_rules: Optional[PermissionRules] = None          # 2.2
    additional_working_dirs: list[str] = field(default_factory=list)  # 3.5 path safety

# session_config.py:192-193
def permission_from_config(cfg, *, default: str) -> str:
    return _normalize_permission(cfg.permission_mode) or default
```

**The project layer (rules)** lands in the `permission_rules` key of `<project>/.openprogram/settings.json` (default project: `<state>/projects/default-settings.json`), read and written through `project_store.load_project_settings` / `save_project_settings` (`store/project/project_store.py:565-`). This is the primary carrier for rules and follows the project. The session-layer `permission_rules` is only a highest-priority one-off override. See `load_merged_rules` (§3.4) for merging.

**Three default values** (the `default` passed to `permission_from_config` decides where an unset session lands; the web and channels paths first consult the project default via `project_defaults(session_id)` and fall back to the table below):

| Entry point | Default | Location |
|---|---|---|
| TurnRequest dataclass field | `ask` | `dispatcher/types.py:53` |
| Web execution path | Project default, else `bypass` | `webui/_execute/__init__.py:552-553` |
| Channels | Project default, else `ask` | `channels/_conversation.py:240-241` |

Subagents are fixed at `bypass` (`sub_agent_run.py:89`): a subagent's lane has no UI subscribed to approval events, so ask would make every tool time out into `[denied]`; and "spawn a subagent" is already an explicit user action.

### 3.7 How plan relates to permission_mode (no prePlanMode)

plan is **visibility control** (hiding write tools via the boolean set in `agent/plan_mode.py`) and does not switch `permission_mode` — the two are orthogonal. So unlike Claude Code, there is no need to "remember the old tier when entering plan and restore it on exit" (CC's plan is a permission tier, and occupying a tier slot is what forces prePlanMode). Entering and leaving plan only flips the `plan_mode._active` switch; the current `permission_mode` (ask/acceptEdits/auto/bypass) never changes and applies as-is on exit — nothing recorded, nothing restored. The code has **no** `pre_plan_permission_mode` field and **no** `permission_context.py`.

---

## 4. Frontend implementation

The frontend does three things: the approval card (receive `question.asked`, render the three choices), the permission mode picker (top-bar permission badge, session level), and the rule management panel (**Projects page**, project level).

### 4.1 Approval card entry point

Approvals merge into unified question rendering (an approval is a question with `kind="approval"`, taking the same path as `runtime.ask`). The entry component `QuestionMode` (`apps/web/components/chat/composer/modes/question/question-mode.tsx`) branches on `kind`: the approval branch (`:82-83, :309-334`) normalizes the frame's `prompt`/`detail`/`risk_level` into a single approval step and renders the card.

`question.asked` frame fields (emitted by the backend `emit_question_asked`, `internals/_approval.py:274-282`):

```json
{
  "type": "question.asked",
  "data": {
    "id": "<uuid hex[:12]>", "session_id": "<may be empty>", "kind": "approval",
    "prompt": "Allow running <tool_name>?",
    "options": ["Allow", "Deny"], "multi": false, "allow_custom": false,
    "detail": "<tool_name>\n<args_json truncated when too long>", "expires_at": 1735689600.0,
    "tool": "<tool_name>", "args": { "...": "tool argument dict" },
    "risk_level": "high"
  }
}
```

`tool`/`args`/`risk_level` are approval-specific (`:281`), letting the frontend draw the danger summary and drive highlighting.

### 4.2 Three-choice approval card + danger highlighting

The approval branch renders three buttons (Allow once / Always allow / Deny) plus danger highlighting (`question-mode.tsx:309-334`):

```tsx
// question-mode.tsx:309-334
if (step.kind === "approval") {
  const pick = (answer as { pick: "once" | "always" | "deny" | null }).pick;
  const risk = step.risk ?? "low";   // "low" | "medium" | "high"
  const label = { once: "Allow once", always: "Always allow", deny: "Deny" } as const;
  return (
    <>
      <div className={styles.prompt}>{withColon(step.prompt)}</div>
      {step.detail ? (
        <pre className={approvalStyles.summary + " " + (approvalStyles["risk_" + risk] ?? "")}>
          {step.detail}
        </pre>
      ) : null}
      <div className={styles.options}>
        {(["once", "always", "deny"] as const).map((p) => (
          <button className={styles.opt + (pick === p ? " " + styles.optPicked : "")}
            onClick={() => onChange({ pick: pick === p ? null : p })}>
            {pick === p ? "✓ " : ""}{label[p]}
          </button>
        ))}
      </div>
    </>
  );
}
```

The approval branch of the `Answer` type is `{pick:"once"|"always"|"deny"|null}` (`question-mode.tsx:74`). Danger highlighting comes from `.risk_high`/`.risk_medium`/`.risk_low` in `approval-mode.module.css` applied to `.summary`; buttons use `.opt`/`.optPicked` (the selected state adds `✓`).

### 4.3 WS payload sent back

The frontend `submit()` sends according to the pick (`question-mode.tsx:162-166`):

```js
wsSend({ action: "question_reply", id: q.id, answer: "Allow", scope: "once" })   // Allow once
wsSend({ action: "question_reply", id: q.id, answer: "Allow", scope: "always" }) // Always allow
wsSend({ action: "question_reject", id: q.id })                                  // Deny
```

Backend handling (`webui/ws_actions/session.py:693-712`) — when `scope` is present it packs `{answer, scope}` into the value, and `await_user_approval` unpacks the scope when consuming it:

```python
# webui/ws_actions/session.py:693-712
async def handle_question_reply(ws, cmd):
    qid = cmd.get("id") or ""; answer = cmd.get("answer"); scope = cmd.get("scope")
    if qid:
        value = {"answer": answer, "scope": scope} if scope else answer
        _resolve_question(qid, "answered", value)

async def handle_question_reject(ws, cmd):
    qid = cmd.get("id") or ""; reason = cmd.get("reason")
    if qid:
        _resolve_question(qid, "declined", reason)
```

`_resolve_question` (`session.py:686-690`) is a thin wrapper over `resolve_question_and_broadcast` (`questions.py`) — the shared claim-once path for WS/REST/channel `/answer`: resolve the registry and broadcast to retract the card in other UIs.

The backend `await_user_approval` returns `(approved, reason, scope)` (`internals/_approval.py:235-305`), with `scope ∈ {"once","always"}`. Flow: `open_question(kind="approval",...)` → `await asyncio.to_thread(ev.wait, timeout)` (does not block the asyncio loop, default 300s) → `consume_or_timeout`: on answered it unpacks `answer`/`scope`, and `answer ∈ {"Allow","approve","yes","y","true","ok"}` → `(True, None, scope)`; declined → `(False, reason, "once")`; timeout → `retract_question` retracts the card → `(False, None, "once")`.

### 4.4 allow-always writes back to project rules

`_approve_then_run` receiving `approved=True and scope=="always"` writes a per-tool allow rule back to the **project** layer:

```python
# openprogram/agent/internals/_approval.py:90-106
def _persist_always_allow_rule(session_id: str, tool_name: str) -> None:
    """Persist tool_name as a per-tool allow rule into the project layer
    (permission_rules.allow in <project>/.openprogram/settings.json).
    Resolves the project via project_for_session(session_id), falling back to get_default_project().
    Rules follow the project -- still in effect after a session switch, remembered long term."""
```

Once written, the next call to the same tool hits allow in `_match_rule` and no longer shows a card. To revoke a mis-clicked "Always allow": delete the entry in the rules panel on the Projects page (§4.6).

### 4.5 Permission mode picker (top-bar permission menu, session level)

The picker is **not in the composer's plus menu**; it is the permission badge
`PermissionBadge` in the chat page top bar (`apps/web/components/chat/top-bar/permission-menu.tsx:68`). The badge is driven by the
`usePermissionMode` hook (`apps/web/components/chat/composer/controls/use-permission-mode.ts`),
which returns `{mode, options, set}`.

The 5 tier labels use Claude Code's official names (`MODE_LABELS` in `use-permission-mode.ts:28-34`, with numeric shortcuts 1-5):

| Internal value | Label |
|---|---|
| `ask` | Ask permissions |
| `acceptEdits` | Accept edits |
| `plan` | Plan mode |
| `auto` | Auto mode |
| `bypass` | Bypass permissions |

**Storage: isolated per session, no global value.** It reads `useBoundComposerSettings().permission_mode`
(`use-permission-mode.ts:48`) — bound to the current session, so switching sessions switches the value.

**Chat frames do not carry `permission_mode`.** The frame builder
`composer/submit/send-chat-message.ts` (`sendChatMessage`) has no `permission_mode` field —
frames carry only text / thinking / tools / web_search / service_tier / attachments and similar.
The backend `webui/ws_actions/chat.py:312` still reads `cmd.get("permission_mode")`, but nothing on the frontend fills it,
so this path is permanently `None`: dead code left uncleaned, not a live mechanism.

**Only two writers actually take effect**, neither through the chat frame:

- **Session settings**: change the session's `SessionRunConfig.permission_mode` (`session_config.py`), persisted into session meta.
- **Project config**: the project-level default tier, supplied by project settings.

Downstream the dispatcher still reads the run config: `effective_permission = permission_from_config(run_cfg, default="bypass")` (`_execute/__init__.py:557`) goes into the TurnRequest.

### 4.6 Rule management panel (Projects page, project level)

The rule management UI lives on the **Projects page** (`apps/web/components/projects/projects-page.tsx:146-148`): expanding a project reveals its rules panel. The panel component `PermissionsSection` (`apps/web/components/projects/permissions-section.tsx`) works by `projectId`:

- Lists the project's deny / ask / allow rule groups, each of which supports manual addition and per-entry deletion.
- Fetching and refreshing go over WS: `list_permission_rules` / `add_permission_rule` / `remove_permission_rule`, with `project_id` on every request; the backend broadcasts a `permission_rules` frame (`session.py:742-748`) to refresh the panel.
- Rule string syntax is `ToolName` or `ToolName(pattern)` (such as `bash(git:*)`), see §2.2.

The backend WS handlers (`webui/ws_actions/session.py:751-783`) are all **project level**: `_resolve_project_id` (`:718-730`) supports requests carrying `project_id` directly (the Projects page knows the project), or resolving the project via `project_for_session` when only `session_id` is present (the composer path); `_mutate_project_rule` calls `save_project_settings` after an add or remove and broadcasts.

> Rules are managed only on the Projects page: settings has no Permissions tab, and the chat composer has no "Manage rules…" entry. Rules uniformly land in the project layer.

---

## 5. Attended mode — an orthogonal mechanism

Attended mode and permissions are two independent mechanisms governing different things:

| Mechanism | Governs | Who triggers it |
|---|---|---|
| **Permission mode** | Whether user approval is needed when the model calls a tool | The model issuing a tool call |
| **Attended** | Whether the model may proactively ask the user a question | The model wanting to call `ask_user_question` |

Attended mode lives in `openprogram/agent/attended.py`. The core (`attended.py:1-23`): a long run either has "someone watching who can answer" (attended) or "nobody around, don't ask" (unattended). The control mechanism is to withhold the question tool from the model when unattended. State: a process-level default `_default = False` (`:33`, unattended by default) plus per-session overrides in `_by_session` (`:34`). Implementation: `denied_ask_tools` (`:64-68`) folds `ask_user_question` into the tool-resolution deny set when unattended, referenced on the runtime side at `runtime.py:1516`. The setter is `set_attended(value, session_id)` (`:38-46`); the web calls it through `handle_set_attended` in `ws_actions/runtime.py:503-504` (per session).

**How they combine**: the permission mode governs "does execution need approval", attended governs "may the model speak up". unattended + bypass = never stops to ask and every tool executes directly (unattended autonomous running); attended + ask = can ask questions and every tool needs a nod (watching closely). The two are orthogonal and combine freely.

---

## 6. Key constraints and code map

### 6.1 Properties that must hold when changing permission code

- **deny/ask before bypass**: in `_gated_execute` (`internals/_approval.py:151-188`), rule-layer deny/ask (① ②) must come before the bypass short circuit (③). Web defaults to bypass, so moving deny/ask after bypass silently ignores "forbid rm -rf" — a security defect.
- **exit_plan_mode forces approval**: `_FORCE_APPROVAL_TOOLS` (`:34`) shows a card even under bypass; submitting a plan requires the user's signature.
- **Scope of modes vs rules**: the permission mode is **session level** (session meta), permission rules are primarily **project level** (`<project>/.openprogram/settings.json`). Do not mix up their storage.
- **Camel-case normalization**: `acceptEdits` is the camel-case canonical value; all comparisons go through the case-insensitive table in `_normalize_permission` (`session_config.py:289-293`). Do not `.lower()` and treat the result as canonical.
- **acceptEdits only allows path-safe write tools**: command-class tools (bash/exec/execute_code) fall through to approval no matter what (⑥ only allows tools with `_accept_edits_safe=True` that pass `_path_is_safe`).

### 6.2 Code map

| Concern | Code location |
|---|---|
| Decision chain `_gated_execute` / `_match_rule` / `await_user_approval` / `_persist_always_allow_rule` / `_risk_level` | `openprogram/agent/internals/_approval.py` |
| Rule string parsing, matching, multi-layer merging | `openprogram/programs/permission_rule.py` (`parse_rule` / `parse_command` / `pattern_matches` / `load_merged_rules`) |
| Path safety / dangerous files and directories / Windows bypass | `openprogram/programs/tools/files/file_safety.py` |
| gate hard block | `openprogram/events/tool_gate.py` |
| Permission mode legal values + normalization + SessionRunConfig fields | `openprogram/agent/session_config.py` |
| `PermissionMode` type + TurnRequest fields and defaults | `openprogram/agent/dispatcher/types.py` |
| Schemaless session meta storage | `openprogram/store/session/session_store.py` |
| Project-level settings read/write + `project_for_session` | `openprogram/store/project/project_store.py` |
| `accept_edits_safe` declaration + per-tool `requires_approval` | `openprogram/programs/_runtime.py`; tool markings in `openprogram/programs/tools/{read,write,edit,glob,grep,list}/` |
| Web default bypass + effective_permission | `openprogram/webui/_execute/__init__.py`; `additional_working_dirs` populated in `_execute/chat.py`, `channels/_conversation.py` |
| WS: approval replies + project rule list/add/remove | `openprogram/webui/ws_actions/session.py`, `chat.py` |
| Attended mode (orthogonal mechanism) | `openprogram/agent/attended.py`, `openprogram/webui/ws_actions/runtime.py` |
| Frontend approval card (approval mode) | `apps/web/components/chat/composer/modes/question/question-mode.tsx` + `../approval/approval-mode.module.css` |
| Frontend permission mode picker (session-level hook) | `apps/web/components/chat/composer/controls/use-permission-mode.ts` + `composer/index.tsx` |
| Frontend rules panel (project level) | `apps/web/components/projects/projects-page.tsx` + `apps/web/components/projects/permissions-section.tsx` |

---

## 7. Explicit non-goals

This lists only what genuinely has no physical carrier, or what would create an unresolvable conflict if added. These are not "skipped for convenience".

- **Enterprise policy layer (policy/flag source)**: no feature flags, no enterprise MDM/policy distribution backend. All three source layers in §2.3 are implemented; only the enterprise layer has no carrier to land in. It can be added when enterprise deployment is supported.
- **local / cliArg rule layers**: no `.openprogram/settings.local.json`, no CLI flags such as `--allow-tool`. The equivalent capability comes from the project layer plus session-layer overrides.
- **External approval delegation (permissionPromptTool)**: delegating the approval decision to an external MCP tool. Everything goes through QuestionRegistry plus the frontend card; this mechanism does not exist. It can be added when custom approval backends are supported.
- **Sandbox isolation (security boundary)**: the permission system is a decision and awareness layer, not a security boundary, and does no process or file isolation. Real isolation is a separate sandbox work stream.
- **Bypass-immune safetyCheck forced approval**: `tool_requires_approval` remains `(bool, reason)` without `classifier_approvable`; path safety only takes effect in the acceptEdits branch, and writing a dangerous file under bypass is not forcibly blocked. `is_dangerous_allow_rule` (`file_safety.py:94-100`) is implemented but unwired. Adding bypass immunity requires extending `tool_requires_approval` into a triple and treating "path unsafe" as ask at point ① of `_gated_execute`.

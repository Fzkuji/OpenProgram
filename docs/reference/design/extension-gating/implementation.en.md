# Implementation map

Where the gating model lives in the code. Use this when touching the implementation.

## File map

```
openprogram/agent/management/
  ├─ gating.py              ← shared helper module (NEW)
  └─ manager.py             ← AgentSpec schema (the canonical struct)

openprogram/agent/
  └─ _model_tools.py        ← gate site for: tools, MCP

openprogram/webui/ws_actions/
  └─ chat.py                ← gate site for: skills (/skill X command)

openprogram/functions/
  └─ __init__.py            ← agent_tools() honours the resolved name list
```

## Shared helper module

**`openprogram/agent/management/gating.py`** — three exports, no other dependencies.

```python
def match_any(name: str, patterns: Iterable[str]) -> bool
    # fnmatch.fnmatchcase wildcard match
    # empty/falsy patterns → False (caller meant "no constraint")

def gate(*, name, category="", disabled=(), allowed=(), categories=()) -> str | None
    # Returns None if the item passes, or a rejection-reason string
    # Resolution order: disabled → allowed → categories

def check_required(installed, required) -> list[str]
    # Returns required patterns that nothing in installed matches
    # Used for MCP "this agent needs server X" hard requirement
```

These are pure functions — no side effects, no globals — and importable from any layer (web, dispatcher, CLI).

## Canonical schema

**`openprogram/agent/management/manager.py:63-86`** — `AgentSpec` dataclass:

```python
@dataclass
class AgentSpec:
    id: str
    name: str = ""
    ...
    skills: dict[str, Any] = field(default_factory=lambda: {
        "disabled": [], "allowed": [], "categories": [],
    })
    tools: dict[str, Any] = field(default_factory=lambda: {
        "disabled": [], "allowed": [],
    })
    mcp: dict[str, Any] = field(default_factory=lambda: {
        "disabled": [], "allowed": [], "required": [],
    })
```

Each block is a plain `dict` so JSON round-trips trivially. Defaults are all-empty (no constraint).

## Gate site 1 — skills (/skill command)

**`openprogram/webui/ws_actions/chat.py:90-116`**

When the user types `/skill X` the handler:

1. Resolves `X` to a `Skill` object (`_skill_resolve`).
2. Loads the agent profile, pulls the `skills` block.
3. Calls `gate(name=resolved.name, category=resolved.category, disabled=..., allowed=..., categories=...)`.
4. If `gate()` returned a rejection string, the chat message becomes a `[error] skill X: <reason>` system message and the skill body is NOT expanded.
5. Otherwise expands SKILL.md into the user turn as before.

```python
from openprogram.agents.gating import gate as _gate
gate_error = _gate(
    name=resolved.name,
    category=resolved.category or "",
    disabled=prof.get("disabled") or [],
    allowed=prof.get("allowed") or [],
    categories=prof.get("categories") or [],
)
if gate_error:
    raise PermissionError(gate_error)
```

## Gate site 2 — tools

**`openprogram/agent/_model_tools.py:174-272`** — `resolve_tools()`.

The function accepts either:
- `wanted: list[str]` — explicit per-turn override (no gating applied, caller already chose).
- `wanted: dict` — `{enabled?, disabled, allowed, toolset?}` shape from the agent profile.
- `wanted: None` — fall through to `agent_tools(source=..., only_available=True)`.

Wildcard gating happens at lines 238-262:

```python
if isinstance(wanted, dict):
    disabled_patterns = list(wanted.get("disabled") or [])
    allowed_patterns = list(wanted.get("allowed") or [])
    ...
    names = [
        n for n in DEFAULT_TOOLS
        if not match_any(n, disabled_patterns)
        and (not allowed_patterns or match_any(n, allowed_patterns))
    ]
```

The earlier `enabled: list[str]` form still wins (it's the explicit override). New `disabled`/`allowed` patterns kick in only when `enabled` is absent.

## Gate site 3 — MCP

**`openprogram/agent/_model_tools.py:192-224`** — `_apply_mcp_gate()`, an inner helper invoked at every return path of `resolve_tools`.

MCP tools surface from `agent_tools()` with names like `slack__send_message` or `github-mcp__create_issue` (server name + `__` + tool name). The gate filters by the `<server>` prefix:

```python
def _apply_mcp_gate(tool_list):
    ...
    def _server_of(name: str) -> str:
        return name.split("__", 1)[0] if "__" in name else ""
    seen_servers = {_server_of(t.name) for t in tool_list if _server_of(t.name)}
    missing = check_required(seen_servers, required)
    if missing:
        return None   # hard fail — agent turn runs with no tools
    out = []
    for t in tool_list:
        srv = _server_of(t.name)
        if not srv:
            out.append(t)             # native tool, no MCP namespace
            continue
        if disabled and match_any(srv, disabled): continue
        if allowed and not match_any(srv, allowed): continue
        out.append(t)
    return out
```

`required` is the **hard** check — if any required pattern matches nothing in `seen_servers`, the whole tool list is replaced with `None`. The dispatcher logs the missing list and the agent runs as tools-disabled for the turn.

## Why three sites, not one

Each gate runs at the point where the LLM is about to see the extension:

| Extension | When does the LLM see it? | Gate site |
|---|---|---|
| Skill | When `/skill X` runs and SKILL.md gets injected into the turn | `chat.py` handler |
| Tool | When `resolve_tools()` builds the `tools=[...]` arg for `agent_loop` | `_model_tools.py` |
| MCP | Same as tool (MCP tools surface through the same `agent_tools()` pipeline) | `_model_tools.py` (`_apply_mcp_gate`) |

We considered putting a single `apply_all_gates(profile, ...)` chokepoint earlier in the stack. We rejected it because the three sites have different "what does the input list look like" — skills have a `Skill` object with a category field, tools are bare strings, MCP tools are namespaced strings. The shared helpers (`match_any`, `gate`, `check_required`) cover ~90% of the logic; only the input shape differs per call site.

## Backward compatibility

Old agent profiles with `skills: ["pdf", "drawio"]` (a bare list, not a dict) are normalised at load time. We already migrate `skills: list` → `skills: {disabled: list}` in `AgentSpec.from_dict`, so existing profiles keep working with no edits.

Similarly `tools: ["bash", "read"]` continues to be valid — the list form is treated as a whitelist (the old `enabled` semantics).

## Testing

There are no dedicated unit tests for `gating.py` yet — `match_any` is `fnmatch.fnmatchcase` + iteration so the logic is one-liner trivial. Integration testing happens through:

- `openprogram/_cli_cmds/doctor.py` — health check enumerates installed skills/tools/MCP and surfaces gating errors at start-up.
- WS smoke test — `/skill X` with a disabled-pattern profile returns the rejection message in the chat transcript.

Add proper unit tests if `match_any` semantics ever diverge from `fnmatch.fnmatchcase` (e.g. if we ever add `**` recursive-glob support).

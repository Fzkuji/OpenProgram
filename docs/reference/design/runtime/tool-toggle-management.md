# Tool toggles and toolset management

> Every `file:line` has been checked against the code. The core principle in one sentence: **a session stores toggle *intent* only, never an expanded list of tool names. The tool array is expanded from the registry at run time, so a newly added tool automatically reaches every historical session.**

---

## 1. How the toolset is determined

### 1.1 The priority chain

The tools handed to the model on a turn are computed by `_resolve_tools(agent_profile, req.tools_override, source)` (`dispatcher/__init__.py:764` → `_model_tools.py:385`). Priority:

| Order | Source | Behavior |
|---|---|---|
| 1 | `override` (per-turn / per-session) | `_model_tools.py:385` `wanted = override if override is not None else profile.get("tools")` |
| 2 | the agent profile's `tools` | used when override is None |
| 3 | neither → `agent_tools(only_available=True)` (= DEFAULT_TOOLS) | `_model_tools.py:386-391` |

The possible values of `override` are handled separately:

- `[]` → all tools off (`:392-393`)
- **`dict`** (`enabled` / `disabled` / `allowed` / `toolset`) → **intent form**, expanded live at run time (`:397-421`). This is the form a session should store.
- **`list[str]`** → `agent_tools(names=[...])`, pinned by name (`:423-428`). Used only to express a small set the user explicitly hand-picked.

Session config is turned into an override by `tools_override_from_config(cfg)` (`session_config.py:82-93`); the consumers are webui `_execute/chat.py:105` and channels `_conversation.py:238`.

### 1.2 The anti-pattern: materializing intent into a list snapshot

If a session expands toggle intent into a `list[str]` at write time, the toolset is frozen at that moment. `handle_chat` in webui does exactly this in **two places** while assembling `tools_flag`, and eliminating them is the point of this design.

**Path A — a non-full tool profile is selected** (`ws_actions/chat.py:321-328`):

```python
if tools_profile and tools_flag is True:
    resolved = _at(toolset=tools_profile, only_available=True)
    if resolved is not None:
        tools_flag = [t.name for t in resolved]   # ← the toolset is expanded early into a list
```

A toolset is supposed to be "store the preset name, expand at run time", but here it is expanded on the spot.

**Path B — Web Search is enabled** (`chat.py:336-356`):

```python
if web_search_flag:
    ...
    elif tools_flag is True:
        base = list(_DEFAULT_TOOLS)        # ← the whole DEFAULT_TOOLS is materialized as a literal
    ...
    tools_flag = base                       # ← tools_flag turns from True/None into list[str]
```

Note `:348-353`: even when `tools_flag is None` ("follow the profile"), enabling web_search sets `base = list(DEFAULT_TOOLS)`, flattening away the "follow the profile" intent too.

> The two paths are independent, and A runs before B. Both have to pass intent through; fixing only one does not eliminate materialization.

### 1.3 What a snapshot does to old sessions

A materialized list is persisted all the way to the DB:

1. `chat.py:482-488` → `save_session_run_config(tools=<list>)`
2. `session_config.py:111-113` `_normalize_tools_value`: list → `(enabled=True, override=[names])`, written to the `tools_enabled` / `tools_override` columns
3. On every later turn: `load_session_run_config` → `tools_override_from_config` hits `:85-86` `if cfg.tools_override: return list(cfg.tools_override)`, **returning the old snapshot verbatim**
4. That list reaches `_model_tools.py:423-428` and goes through `agent_tools(names=[...])`, **honoring only the names in the snapshot**

**Consequence**: after a new tool is added to DEFAULT_TOOLS (`functions/__init__.py:69`) — list_agents or send_message, say — **every session that ever enabled web_search or picked a non-full profile** keeps its original name list forever and never sees the new tool.

By contrast, a session that never touched either toggle stores `tools_enabled=True` (a bool), and `:87-90` returns `list(DEFAULT_TOOLS)` live on each turn, so new tools appear automatically. **The difference is "store a bool / intent" versus "store a materialized list".**

---

## 2. How other projects do it (the consensus)

| Project | What is stored | How staleness is avoided |
|---|---|---|
| **opencode** | per-session `tools: Record<name, boolean>` intent map | the tool table is built fresh from the live registry each turn, then filtered by intent (`config.ts:552`, `session/tools.ts:86`) |
| **claude-code** | `--tools` (a selection, with sentinels `""`=none / `"default"`=all) plus `--allowedTools` / `--disallowedTools` (allow/deny pattern strings) | the builtin set is a constant; only selection / allow / deny intent is stored, and the intersection is taken at run time (`main.tsx:988`) |
| **pi-ai** | builtin toggles and extension tools managed separately | turning builtins off is boolean intent and does not affect live extension loading |
| **hermes** | a named preset (toolset name) | stores the preset **name** and expands at run time (our `TOOLSETS` is ported from it) |

**The consensus, without exception: all of them store toggle intent (booleans, allow/deny, or preset names) and never freeze an expanded tool list.** The real tool array is always expanded from the live registry at request time.

---

## 3. The core principle: store *intent*, not a *list*

The minimum intent a session should store:

| Field | Meaning | Values |
|---|---|---|
| tools on/off | the master toggle | `True` / `False` / `None` (follow the profile) |
| web_search | **layered on top of** the master toggle's result | `bool` |
| preset name | which toolset was picked | `"full"` / `"research"` / … / `None` |
| explicitly disabled | the few tool names turned off by hand | `list[str]` (short) |

At run time this feeds the existing dict-override channel (`_model_tools.py:397-421`) and is expanded live. As a result: a new tool reaches old sessions on their next turn; deleted tools and preset changes are followed automatically; the stored payload is O(the few things the user changed). The existing dict-override branch and the `tools_enabled=True` bool branch **are already in this form** — only the two materializing paths above have degraded into lists.

---

## 4. Effects on context, cache, and history (checked item by item)

1. **Does the tool array count as tokens**: ContextCommit's `total_tokens` **excludes** the tool array (the commit dataclass has no tools field, `commit/types.py:104-140`), but the provider request **is billed** for the tool array (`anthropic.py:601`, sent with the request). So toolset size is invisible to the compaction budget but visible in the real input cost of every turn.
2. **Caching**: the tool array sits at the **root of the cache prefix** (`cache_policy.py:80-89`; the first breakpoint is placed at the last tool, Anthropic/Bedrock explicit mode only). So **any addition, removal, or reordering of the tool array rewrites the cache prefix and misses the entire prompt cache**. **Hard constraint: expansion must be deterministic** (stable sort plus dedup) so the same intent expands byte-identically every time and the cache hits.
3. **Calls present in history but absent from the current tool array**: historical tool_use / tool_result are rendered from the message (`anthropic.py:328-451`), independent of the current `context.tools`, and replay fine — the model simply cannot issue that call on this turn. So **do not filter or rewrite historical tool_use based on the current tool array** (breaking tool_use↔tool_result pairing causes a 400).
4. **ContextCommit replay is unaffected**: commits do not store the tool array; the toolset is a request-time artifact. Removing the tool snapshot from storage therefore **changes no commit replay semantics**, which keeps the risk of the change low.

---

## 5. Three places that carry the design

**A — chat.py passes intent through** (`ws_actions/chat.py:321-328` and `:336-356`):

- Profile path: pass the **preset name** through (via the `toolset` field of the dict override) instead of expanding it into `[t.name for t in resolved]`.
- web_search path: pass web_search through as **layered intent**; `tools_flag` stays True/None and is not rewritten into a list.

**B — session_config stores intent**:

- `SessionRunConfig` (`session_config.py:12-18`) carries `web_search: Optional[bool]` and a preset-name field.
- load/save (`:20-79`) read and write both.
- `tools_override_from_config` (`:82-93`) emits a **dict intent** rather than a list: `tools_enabled is False` → `[]`; otherwise → `{"enabled": True if enabled else None, "toolset": <preset>, "disabled": [...], "web_search": <bool>}`. No `list[str]` snapshot is written.

**C — the dict override supports layering web_search**:

The dict-override branch (`_model_tools.py:397-421`) has to recognize a `web_search` key in addition to `enabled` / `disabled` / `allowed` / `toolset`. Because web_search is not in DEFAULT_TOOLS, storing the intent while the dict branch ignores the key would produce an expansion without web_search, and the toggle would do nothing. So C is a prerequisite for B: if web_search is missing after expansion, `agent_tools(names=[...]+["web_search"])` supplies it. A provider's builtin web_search (the `openai_codex.py:376` kind) is an alternative path whose availability varies by provider (see §8).

**`list[str]` means "the user hand-picked these"** (for example `["web_search"]` for a web-search-only session): `tools_override_from_config` passes it through verbatim and the list branch of `_model_tools.py` expands it by name. It no longer carries a materialized snapshot of all tools — "all tools" is always expanded live from `{enabled: True}` intent.

---

## 6. Properties that should hold

When the design is correct, all of the following hold at once, and they are also what regression testing looks at:

- **Deterministic expansion**: expanding the same intent twice in a row yields element-wise equal results (stable ordering plus dedup), avoiding the cache thrash of §4.2.
- **Intent round-trip**: storing `web_search=True` reads back True; an old session lacking the field reads back None without error.
- **Dict output**: `enabled=True` → a dict containing enabled; with web_search → the expansion contains web_search; `enabled=False` → `[]`.
- **Writes do not materialize**: after a new session enables web_search or picks research, `tools_override` in the DB is NULL or a dict, never a full list.
- **New tools appear automatically**: expanding `{enabled: True}` intent includes the tools most recently added to DEFAULT_TOOLS (send_message, list_agents).
- **Cache stability**: a session with unchanged intent sends two turns back to back and provider usage shows a cache hit on the second, with no toolset thrash.
- **All writers agree**: no second `list(_DEFAULT_TOOLS)` or `[t.name for t in` materialization exists anywhere in the repo; webui, channels, and the TUI (`session.py`, `apps/cli/src/ws/client.ts`) all pass intent through wherever web_search can be enabled or a profile picked.

---

## 7. Deferred loading: availability is not residency

§4.1 established that the tool array is billed on every turn. Toggles decide
which tools are **available**; deferred loading decides which of those pay for
their JSON Schema on every turn. These are separate axes, and conflating them
is what made the resident cost grow with the tool count.

### 7.1 The two states of an available tool

An available tool ships in one of two forms:

- **Resident** — full JSON Schema in the provider's tools array, every turn.
- **Deferred** — one bare `name` line in the system prompt's catalog. The
  model loads the schema on demand by calling `tool_search`, after which it
  is resident from the next turn to the end of the session.

The catalog carries names only, not descriptions. A name is enough for the
model to recognise a candidate and ask for it, and it keeps the whole
catalog to a couple of hundred tokens no matter how many tools are deferred.

A deferred tool is **not disabled**. The model can still call it; it just has
to load the schema first. A disabled tool is absent from both the array and
the catalog and cannot be called at all.

### 7.2 The split

`split_tools_for_dispatch(tools)` (`functions/_runtime.py`) partitions the
resolved toolset into `(provider_tools, catalog)` by reading each tool's
`_defer` sidecar attribute, minus the session's already-loaded set:

- not deferred → provider array
- deferred and in the session's loaded set → provider array
- deferred and not yet loaded → catalog

The loaded set lives in a ContextVar (`install_loaded_deferred`, seeded per
session by the dispatcher; `mark_deferred_loaded` adds to it from inside
`tool_search`). Which entries of that set actually reach the array is decided
once per turn — see §7.4.

### 7.3 Which tools are deferred

`apply_default_deferral()` runs once at import and marks
`_defer = name not in RESIDENT_TOOLS`. Two groups end up deferred:

1. Everything in the `full` exposure whitelist but outside `DEFAULT_TOOLS` —
   memory, worktree, browser, image, and the other cold tools. These were
   never in the default set, so deferral costs nothing.
2. `DEFERRED_DEFAULT_TOOLS` — tools that stay in `DEFAULT_TOOLS` (available
   by default) but are big and rarely called, so they are not resident:

   | tool | schema | why it is not resident |
   |---|---|---|
   | `playwright_browser` | ~1170 tok | Largest single schema. Browser automation is an explicit, narrow intent; a coding session never touches it. |
   | `enter_plan_mode` | ~1050 tok | Plan mode is normally entered by the user via the tier chip / TUI (`plan_mode.sync_tier`), which does not go through this tool. The model entering plan mode on its own judgement is the rare path. |
   | `exit_plan_mode` | ~640 tok | Only meaningful while plan mode is active, and the plan-mode prompt block names it explicitly, so the model knows to load it. |
   | `send_message` | ~380 tok | Cross-session/branch messaging, only used in multi-branch collaboration. |

`tool_search` itself is never deferred — it is the only way to load anything
else, so deferring it would be a deadlock. This is asserted twice: the
`RESIDENT_TOOLS` union and an explicit guard in `apply_default_deferral`.

Effect: the default resident array drops from ~7.9k to ~4.7k tokens per turn,
a ~41% cut, with no tool becoming unavailable.

### 7.4 The tools array has turn granularity

The tools array is the **root of the cached prefix** for both Anthropic and
OpenAI — the cache breakpoint sits on the last tool entry, so everything
after the array (system prompt, memory, the entire history) is cached behind
it. Change the array and every one of those tokens is re-read at full price.

Therefore the array is **frozen at the turn boundary**. `freeze_turn_tools`
runs once per turn in the agent loop's outer loop and pins the set of
deferred tools eligible for the array; `split_tools_for_dispatch` reads that
frozen set instead of the live loaded set. A `tool_search` call in the middle
of a turn adds to the loaded set but cannot grow the array — the array, and
the prefix rooted on it, stay byte-identical for every provider call of that
turn. The next `freeze_turn_tools` promotes whatever accumulated.

Without the freeze, one `tool_search` invalidated the prefix for the whole
remainder of the turn — and a turn is exactly when tool loading happens, so
the invalidation landed at the worst possible moment. Measured over two days
of real traffic this accounted for 53% of input tokens missing the cache.

**Availability does not wait for the array.** Two paths make a tool loaded
mid-turn callable in that same turn:

1. `tool_search` returns the loaded tools' **full schemas** in its result
   text, in the same `{"name", "description", "parameters"}` shape as an
   array entry, with an explicit note that they can be called immediately.
   The model constructs the call from that text.
2. Dispatch resolves by name against the **complete tool list**, not the
   provider array (`agent_loop._execute_tool_calls`). A call for a tool that
   is loaded but not yet in the array executes normally.

So the freeze changes when a tool is *advertised in the array*, never
whether it can be *used*. The cost of the promotion — one prefix rewrite —
is paid once per tool per session, at a turn boundary where a fresh user
message has already changed the tail anyway.

This is also why the defer list targets tools that are rarely used rather
than tools that are merely large: a tool loaded in most sessions would trade
a fixed saving for a recurring cache miss.

Callers that build a provider array outside any turn (budget accounting,
`breakdown`, tests) call `release_turn_tools()` to fall back to the live
loaded set.

### 7.5 The catalog is an assembler component

The catalog is not hand-appended by the dispatcher. It is a registered
context component (`deferred_catalog`, L1 order 25, in `context/components.py`)
built by `_build_deferred_catalog`, so the string the engine budgets is the
string that ships. The component reads the tools from a ContextVar and returns
empty when nothing is deferred.

Budget accounting follows the same split. `_estimate_one_tool`
(`context/budget.py`) prices each tool as what that tool actually puts on
the wire:

- **resident** — `description` **and** `parameters` schema, plus a small
  wrapper. Both halves matter: the descriptions are roughly 45% of the
  resident cost, so pricing the schema alone understates the array by
  several thousand tokens.
- **deferred** — its bare name plus a newline, matching
  `deferred_catalog_text`. Pricing a deferred tool by its description
  overstates the catalog by roughly an order of magnitude, because the
  description is precisely what the catalog does not send.

Getting either half wrong is easy to miss, because the two errors point in
opposite directions and a plausible-looking total can hide both.
`tests/unit/context/test_budget.py` therefore checks each half against a real
tokenized wire payload separately, within 15%.

### 7.6 Properties that should hold

Regression coverage lives in `tests/unit/context/test_tool_defer.py`:

- no member of `DEFERRED_DEFAULT_TOOLS` appears in the resident array of a
  fresh session, nor in `RESIDENT_TOOLS`
- every one of them appears in the catalog, and in the assembled system prompt
- `tool_search` moves a deferred tool into the provider array and out of the
  catalog
- `tool_search` is never deferred; `apply_default_deferral` is idempotent
- within a turn the provider array and the catalog are unchanged by
  `tool_search`; the loaded tool joins the array at the next turn boundary
- `tool_search` returns the full parameter schema, and a tool loaded but not
  yet in the array still resolves for dispatch

---

## 8. Known boundaries

- **Provider builtin web_search vs. web_search in the tool array** (an either/or in change C): codex / OpenAI Responses is confirmed to use the builtin `opts["web_search"]` (`openai_codex.py:376`); Anthropic and other providers need checking one by one. Until that is settled, the safe "web_search layered in as a tool name" path stays.
- **The `allowed` semantics of the dict override**: today `allowed` (`_model_tools.py:406`) filters DEFAULT_TOOLS rather than the full set. This change does not extend that.
- **The exact token count of the tool array** is a server-side figure and cannot be measured statically in this repo; all that is confirmed is that it is billed and sits in the cache prefix.

---

## 9. Implementation status

### 9.1 Carrying files

| File | What it carries |
|---|---|
| `openprogram/agent/session_config.py` | the `web_search` / `toolset` intent fields of `SessionRunConfig`; `save`/`load` for them (stored in git session meta, so no DB schema change — `update_session(**fields)` passes arbitrary keys through); `tools_override_from_config` emits **dict intent** (`{enabled, toolset, web_search}`) for live expansion instead of materializing the tool table; `list[str]` is reserved for explicit user picks and passed through verbatim |
| `openprogram/webui/ws_actions/chat.py` | passes `tools_profile` / `web_search_flag` through as **intent** to `save_session_run_config(toolset=, web_search=)`; the single-element list from "tools=False + web_search=True → `["web_search"]`" is an explicit user pick, not a full snapshot |
| `openprogram/agent/internals/_model_tools.py` | web_search layering in the dict-override branch (`resolve_tools`, ~397-421): `_overlay_web_search` adds web_search after expansion via either the toolset or the names path when the intent asks for it and the result lacks it |
| `openprogram/programs/_runtime.py` | the deferred machinery: `_defer` sidecar, the loaded set, `freeze_turn_tools` / `release_turn_tools` (§7.4), `split_tools_for_dispatch`, `tool_search` and the schemas it returns, `deferred_catalog_text` |
| `openprogram/agent/agent_loop.py` | calls `freeze_turn_tools` once per turn at the top of the outer loop; resolves tool calls by name against the full tool list so a mid-turn load is still dispatchable |

### 9.2 Key design points (do not break)

- **Expansion must be deterministic**: the tool array is at the root of the prompt cache prefix, and any ordering wobble misses the whole cache. Today `agent_tools` returns in names/registry order, which is naturally stable, and `tests/unit/programs/test_tool_expansion_deterministic.py` locks that in. **When changing `agent_tools` / `_filter_agent_tools` later, do not introduce `set()` iteration or dict churn that breaks the order** — the cache would fail silently (no error, just quietly more expensive).
- **Never materialize "all tools" into a list stored on the session**: all tools are always expanded live from `{enabled: True}` intent. `list[str]` denotes only the few tools a user explicitly picked. This is the design's hard line.
- **Do not touch history**: tool toggles govern what can be called next, and never filter or rewrite historical tool_use (that would break tool_use↔tool_result pairing and cause a provider 400).
- **The tools array changes only at turn boundaries** (§7.4). Anything that grows it mid-turn discards the cached prefix for the rest of that turn. When a tool needs to become usable sooner, hand the model its schema in a tool result — do not append to the array.

### 9.3 Tests (regression protection)

- `tests/unit/programs/test_tool_expansion_deterministic.py` — deterministic expansion (stable cache prefix)
- `tests/unit/store/test_session_config_tools_intent.py` — intent round-trip, verbatim pass-through of user-picked lists, and end to end: the expanded intent includes new tools (send_message / list_agents) and web_search layering takes effect
- `tests/unit/store/test_session_config.py::test_tools_enabled_yields_live_intent_not_snapshot` — `tools=True` produces `{enabled:True}` intent rather than a list snapshot
- `tests/unit/context/test_tool_defer.py` — the deferral properties of §7.6, including the turn-boundary freeze
- `tests/unit/context/test_budget.py` — each half of the tool pricing against a real tokenized payload (§7.5)

### 9.4 Extension points

- **Provider builtin web_search** (§8): web_search currently uses the "layer in the tool name" path; once builtin support is confirmed per provider, it can be switched at `_overlay_web_search`.
- **New intent dimensions** (restricting tools per channel, say): add a key to the dict intent and handle it in the dict branch of `resolve_tools` — do not revert to storing an expanded list.

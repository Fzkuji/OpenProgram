# Open Items Where Design Docs Are Out of Sync With Code

Audit date: 2026-06-18 (second audit)

This file records the divergences between the design docs and the actual code, ordered by priority. Once a divergence is fixed, delete its entry here.

---

## ~~Path errors~~ (fixed)

### ~~extension-gating/implementation.md~~
- ~~Paths written in the doc: `openprogram/agents/gating.py`, `openprogram/agents/manager.py`~~
- ~~Actual paths: `openprogram/agent/management/gating.py`, `openprogram/agent/management/manager.py`~~
- Status: ✅ Corrected; the doc paths are now right.

---

## Docs that need updating (HIGH)

### providers/models/thinking-effort.md
1. **The Opus 4.7 override entry in the §10 open items is stale**: the doc treats the `["low","medium","high"]` restriction as a bug,
   but this is a deliberate design choice by Anthropic (Claude 4.6 guidance). Either delete this open item or rewrite it to explain the design rationale.
2. **The "max" level mapping is marked incorrectly**: the doc claims the max mapping for 5 providers is "unmapped", but in the actual code
   `anthropic.py` already has the `xhigh → max` mapping, and every provider supports the max level. Update the mapping table.
3. **Fable 5**: the doc mentions that the Fable 5 description is missing, but `thinking_catalog.py` has no Fable 5 entry either.
   Need to confirm: is it missing from the code, or did the doc write too much? If the model already exists in the models.dev catalog but is not recorded in the catalog, add it.

---

## Stale status markers (MEDIUM)

### memory/memory-v2.md
- Phase 0-1: complete (doc marker is correct)
- Phase 2: the doc marks it "❌ not started", but §0.5 also mentions "the pre-read layer has landed" (the Provenance dataclass)
- Should clarify: split Phase 2 into substeps and mark which substeps are already partial and which are still to do.

### ~~context/contextgit.md → merged into context/storage-and-engine.md~~
- ~~Doc marker: "Status: proposal, not implemented"~~
- Status: ✅ Merged into `context/context.md` together with context-commit-chain / context-engine-spec / context-attach-merge / cross-turn (the DAG foundation lives in `contextgit/dag.py`; the upper layer is not yet built).

---

## Implementation lag (design is valid but the code has not fully caught up)

### context/cross-turn-tool-context.md
- The "tool aging + one-line semantic stub" strategy is fully described in the doc
- `openprogram/context/tool_aging/` exists but the implementation diverges from the doc
- Sync the doc once the implementation is finished.

### providers/model-catalog-final.md
- The full pipeline of models.dev auto-update TTL + overwrite-save of the fetched data has not fully landed
- The model-list fetch logic exists but the auto-refresh mechanism is not implemented.

---

## Missing content

### runtime/ is missing a process_runner design doc
- `agent/process_runner.py` is an important subprocess-execution module (spawn, stop, user-input bridge)
- There is no corresponding design doc.

### runtime/ is missing a dispatcher design doc
- `agent/dispatcher/__init__.py` is a 530-line core module
- There is no standalone design doc (dispatcher-split.md only discusses the split, it is not a full design).

---

## Docs confirmed correct in this audit

The following docs were audited and are fully consistent with the code; no changes needed:

- `runtime/controllability-and-three-surface-sync.md` — attended/unattended, graceful stop, and three-surface sync are all implemented
- `runtime/user-input-requests.md` — Phase 1+2 have landed (QuestionRegistry, the three Transports including the newly added TTYTransport)
- `function/function-calling-unification.md` — already uses the "profiles" terminology, consistent with the code
- `extension-gating/implementation.md` — paths are now correct
- `context/context.md` (merged from contextgit and four others + the overview diagram) — status markers are correct

---

## Open problems in the tool-calling system

### 1. Cause of wiki_agent self-recursion (✅ identified)
- **Root cause**: `research_harness/wiki/wiki_agent.py:122` calls `runtime.exec(content=[task])` bare——
  it does not pass toolset/tools, so it defaults to `DEFAULT_TOOLSET="full"` (98 tools, including wiki_agent itself).
  The model sees wiki_agent's tool description ("Maintain a wiki vault — route to ingest...")
  which happens to match the current task ("research long horizon agent") → decides it needs to call wiki_agent → calls itself →
  inside it is another bare exec that again sees itself → infinite recursion. Each level returns
  `{'error': "'info|warning|success|error'"}` (wiki's internal enum validation failure),
  the upper-level model receives the error → retries and calls itself again.
- **Resolved (commit `1f6f5fce`)**: changed from "self-deny by hiding the tool" to "situational guidance + a recursion-depth ceiling as a backstop".
  - The situational hint (`runtime._situational_prefix`) is injected at the start of the user turn, telling the model "you are inside X, calling X = infinite recursion, use the lower-level tools", with the docstring demoted to the back → directly negating the premise "this should route to wiki_agent".
  - Backstop: `_MAX_AGENTIC_RECURSION_DEPTH=5`, counted per function name; exceeding 5 levels for the same name raises `RecursionError`.
  - self-deny has been removed, the tool list contains the function itself, relying on guidance rather than hiding.
  - Design doc: `docs/design/runtime/execution/agentic-self-recursion.md`; tests: `tests/agentic_programming/test_self_recursion_guard.py` (8 cases).
- **Remaining** (to do, see #2): scoping the toolset for each harness's exec + detecting cross-function cycles (A→B→A) (currently only direct self-recursion is guarded).
- Session record: `~/.openprogram/sessions/local_d125e9a9c3/history/`
  the context_tree shows 7 levels of nesting (4d76→0c07→0964→c6f9→f1c9→4379→8746→100c).

### 2. Whether harness-internal toolsets need to be restricted
- Problem: for harnesses like wiki_agent/research_agent/gui_agent, should their own internal exec see only "the tools needed to do their actual job", rather than the full set?
- Current state: full set by default (full); self-deny only blocks a harness from calling itself; one harness can still call another (wiki calls research, research calls gui) — which can lead to "going off track".
- Decision pending: (a) the framework stays out of it, each harness restricts the toolset in its own exec; (b) the framework automatically denies all harness entry points (wiki/research/gui) while a harness is running; (c) keep the status quo and only guard self-recursion.
- Reference: Claude Code's subagents use an allowlist to restrict tools, precisely to prevent this kind of going off track.

### 3. Tool Profile selection does not yet affect actual tool resolution
- Problem: the chat-box profile picker lets you choose a profile and the backend persists the active profile, but **after picking a profile the tools actually used in that conversation are still decided by the Tools toggle (on/off)**, the profile's tool list is not sent to the dispatcher as tools_override.
- Fix: the WS chat action passes the active profile name → the dispatcher resolves it with `agent_tools(toolset=<profile>)` → only that set of tools is provided.
- Location: `webui/ws_actions/chat.py:313-316` (the tools_override logic) + the submit function in `composer/index.tsx`.

### 4. Splitting the Functions page into Agentic/Built-in tabs (in progress)
- Design: a tab bar at the top (similar to the Wiki/Journal/Core on the Memory page), splitting into Agentic (function management + folders) and Built-in Tools (profile management).
- Current state: the tab bar is added, tab state is added, the sidebar is hidden on the builtin tab, agentic content is hidden on the builtin tab, and tools show only on the builtin tab. CSS is added.
- To do: typecheck + build + browser verification, to confirm the per-tab rendering is correct.

### 5. ~~The Functions page delete action still uses the native confirm()~~
- ✅ Fixed: searched and confirmed there are no leftover native `confirm()` calls; all have been replaced with ConfirmDialog.

### 6. ~~The tool right-click menu on the Functions page is unreasonable~~
- ✅ Fixed: `functions-page.tsx:579` is now `tab === "agentic" ? contentCtx : undefined`, so the builtin tab does not trigger contentCtx.

---

## Open problems in the Agentic Function runtime

### 7. Checkpoint resume
- **Problem**: when `runtime.exec` inside an agentic function fails all 6 consecutive retries (provider unreachable), it raises directly, the function terminates, and it cannot be recovered.
- **Current state**: the DAG state is complete (the frame node is marked `status="error"`, all child nodes are preserved), `_render_history_messages` loads history from the DAG, and the infrastructure is in place.
- **Plan**: provide a `resume_function(session_id, node_id)` entry point — set the frame node's status back to `running`, re-call `runtime.exec` with the same frame_node_id, and the DAG history is automatically reconnected. Add a "retry" button in the webui to trigger it.
- **Core change**: needs a "re-entry" entry point + restoring the contextvars (_call_id, etc.) + rebuilding the runtime/agent context.
- **Location**: `agentic_programming/function.py` (the wrapper layer), `agentic_programming/runtime.py` (the exec layer).

### ~~8. Bash tool does not track file modifications~~
- ✅ Resolved (`69432d88`): triggered through the unified entry point — `_execute_tool_calls` diffs the file state before and after bash runs, and automatically makes a checkpoint for any changed files.
- Known limitation: it currently only scans the top-level files of the cwd, subdirectory changes are not covered (to be changed to a recursive scan later).
- Additionally, the ④ system-level sandbox (`cf2edde5`) also restricts at the source the range of files bash can touch.

---

## ~~Other open items~~ (fixed)

### ~~research_agent's bad default `toolset=("harness",)`~~
- ✅ Fixed: changed to `toolset=("research",)` (2026-06-18).

### ~~The stale comment on the "full" static list in the design doc~~
- ✅ Fixed: the `functions/__init__.py` TOOLSETS["full"] comment is updated, explaining that full is now just a named preset and that exposure is collected dynamically by `exposed_names()`.

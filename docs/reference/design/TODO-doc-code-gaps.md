# Open Items Where Design Docs Are Out of Sync With Code


This file records the divergences between the design docs and the actual code, ordered by priority. This file retains only unresolved divergences; historical fixes are recorded in Git.

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


---

## Missing content

### runtime/ is missing a process_runner design doc
- `agent/process_runner.py` is an important subprocess-execution module (spawn, stop, user-input bridge)
- There is no corresponding design doc.

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
- **Remaining** (to do, see #2): scoping the toolset for each harness's exec + detecting cross-function cycles (A→B→A) (currently only direct self-recursion is guarded).
### 2. Whether harness-internal toolsets need to be restricted
- Problem: for harnesses like wiki_agent/research_agent/gui_agent, should their own internal exec see only "the tools needed to do their actual job", rather than the full set?
- Current state: full set by default (full); self-deny only blocks a harness from calling itself; one harness can still call another (wiki calls research, research calls gui) — which can lead to "going off track".
- Decision pending: (a) the framework stays out of it, each harness restricts the toolset in its own exec; (b) the framework automatically denies all harness entry points (wiki/research/gui) while a harness is running; (c) keep the status quo and only guard self-recursion.
- Reference: Claude Code's subagents use an allowlist to restrict tools, precisely to prevent this kind of going off track.

### 3. Tool Profile selection does not yet affect actual tool resolution
- Problem: the chat-box profile picker lets you choose a profile and the backend persists the active profile, but **after picking a profile the tools actually used in that conversation are still decided by the Tools toggle (on/off)**, the profile's tool list is not sent to the dispatcher as tools_override.
- Fix: the WS chat action passes the active profile name → the dispatcher resolves it with `agent_tools(toolset=<profile>)` → only that set of tools is provided.
- Location: `apps/server/openprogram_server/_webui/ws_actions/chat.py:313-316` (the tools_override logic) + the submit function in `apps/web/components/chat/composer/index.tsx`.

### 4. Splitting the Programs page into Agentic/Built-in tabs (in progress)
- Design: a tab bar at the top (similar to the Wiki/Journal/Core on the Memory page), splitting into Agentic (function management + folders) and Built-in Tools (profile management).
- Current state: the tab bar is added, tab state is added, the sidebar is hidden on the builtin tab, agentic content is hidden on the builtin tab, and tools show only on the builtin tab. CSS is added.
- To do: typecheck + build + browser verification, to confirm the per-tab rendering is correct.

### 7. Checkpoint resume
- **Problem**: when `runtime.exec` inside an agentic function fails all 6 consecutive retries (provider unreachable), it raises directly, the function terminates, and it cannot be recovered.
- **Current state**: the DAG state is complete (the frame node is marked `status="error"`, all child nodes are preserved), `_render_history_messages` loads history from the DAG, and the infrastructure is in place.
- **Plan**: provide a `resume_function(session_id, node_id)` entry point — set the frame node's status back to `running`, re-call `runtime.exec` with the same frame_node_id, and the DAG history is automatically reconnected. Add a "retry" button in the webui to trigger it.
- **Core change**: needs a "re-entry" entry point + restoring the contextvars (_call_id, etc.) + rebuilding the runtime/agent context.
- **Location**: `openprogram/agentic_programming/function.py` (the wrapper layer), `openprogram/agentic_programming/runtime.py` (the exec layer).

### 8. Bash tool file-modification tracking
- Known limitation: it currently only scans the top-level files of the cwd, subdirectory changes are not covered (to be changed to a recursive scan later).
- Additionally, the ④ system-level sandbox (`cf2edde5`) also restricts at the source the range of files bash can touch.

---

# Project workspace — files, tabs, and multi-session

The web UI is a workspace, not just "chat with a project chip": the
project's files are browsable and viewable in multiple tabs, a project
carries several sessions, and the chat page has a per-session overview
panel (outputs / subagents / sources). The layout follows the three-pane
shape hosted agent products use — chat left, tabbed file viewer center,
file tree right, with the project list as an expandable table.

## 1. What already exists (reuse, don't rebuild)

| Asset | Where | Reused for |
|---|---|---|
| Project entity layer (id/name/path/sessions, settings.json) | `openprogram/store/project_store.py` | everything |
| Project WS actions (list/create/remove/config/sessions/workdirs) | `openprogram/webui/ws_actions/project.py` | list page, workspace |
| `/projects` page (list + settings/sessions/info tabs) | `web/components/projects/projects-page.tsx` | evolves into the new list page |
| Chat component tree (composer, messages, top-bar) | `web/components/chat/` | workspace left pane |
| Right sidebar shell (history/detail/context views) | `web/components/right-sidebar/` | chat overview panel |
| Memory page editor (edit/preview mode, save) | `web/components/memory/` | file editing (slice 5) |
| `wsRequest` helper + ws action registry | `web/lib/net/ws-request.ts`, `webui/server.py` | all new APIs |
| `/api/pick-folder` native folder picker | `web/app/api/pick-folder` | add-project flow |

The main missing pieces are (a) a **file API** scoped to a project, and
(b) the chat view being **mountable by sessionId** instead of owning the
whole route.

## 2. Backend: project file API

New module `openprogram/webui/ws_actions/files.py`, registered like the
other action modules.

| Action | Request | Reply |
|---|---|---|
| `project_file_tree` | `project_id`, `path` (relative dir, `""` = root) | one directory level: `[{name, type: file\|dir, size, mtime}]` — lazy, one level per call, so huge repos stay cheap |
| `project_file_read` | `project_id`, `path` | `{content, size, mtime, truncated}` for text; `{binary: true}` / `{too_large: true}` guards |
| `session_artifacts` | `session_id` | `{outputs: [...], subagents: [...], sources: [...]}` (see §5) |

Slice 5 adds `project_file_write`, `project_file_create`,
`project_file_rename`, `project_file_delete`.

One HTTP route on the existing Starlette app in `webui/server.py` for
bytes that don't belong in JSON frames:

```
GET /files/raw?project_id=...&path=...   → images, downloads
```

**Safety rules** (single `_resolve(project_id, path)` helper, every
action goes through it):

* `os.path.realpath` result must be inside the project path or one of
  the session's `workdirs` — otherwise reject. This is the path-traversal
  gate.
* Read cap ~1 MB for the viewer; larger files answer `too_large` and the
  UI offers the raw-download link.
* Binary sniff (null byte in first 8 KB) → `binary: true`.
* Dotfiles are listed; `.git/`, `node_modules/`, `.venv/`, `__pycache__/`
  are shown but collapsed-by-default (the tree simply doesn't prefetch
  them — free, since loading is per-level anyway).

## 3. Workspace route: `/projects/[id]`

Next route `web/app/(shell)/projects/[id]/page.tsx`, three panes:

```
┌────────────┬──────────────────────────┬──────────────┐
│  Chat      │  [tab] [tab] [tab]  [+]  │ filter…      │
│  (session) │  breadcrumb  path        │ ▸ src        │
│            │  ┌────────────────────┐  │ ▸ docs       │
│  composer  │  │ file viewer        │  │   file.md    │
└────────────┴──┴────────────────────┴──┴──────────────┘
```

* **Right — file tree.** Lazy per-directory loading via
  `project_file_tree`; filter box does a client-side match over loaded
  nodes. Click file → opens/focuses a center tab.
* **Center — tab strip + viewer.** Tab state in a small zustand store,
  persisted to `localStorage` keyed by project id (reopen the workspace,
  your tabs are back). Viewers by extension: code/text with line numbers
  + syntax highlight, markdown with rendered/source toggle, images via
  `/files/raw`, everything else a download card. Read-only until slice 5.
* **Left — chat.** The existing chat view mounted with an explicit
  `sessionId`, plus a session switcher in its header: the project's
  sessions (from `list_project_sessions`) in a dropdown + "new session"
  (created pre-bound to the project via `set_session_project`).
  Multi-session = fast switching within the workspace; the sidebar's
  recents keep working as before.

**Agent ↔ files linkage** (cheap, high value): file paths in tool-call
rows of the transcript become clickable and open in the center tabs —
watch the agent edit, click, see the file.

## 4. Projects list page: expandable table

`/projects` becomes a table — Name / Sources (path) / Updated — where a
project row expands inline to its sessions (already available via
`list_project_sessions`). Click a session → `/projects/[id]?session=...`.
Row actions: open workspace, new session, ⋯ menu (rename, settings,
remove). The current settings/info tab content moves into the ⋯ →
settings dialog; nothing is lost, the page just stops being a
master-detail split.

Backend additions: `updated_at` on the project dict (max of its
sessions' timestamps, falling back to registry ctime) and a
`rename_project` action. Pinning can wait.

## 5. Chat page: session overview panel

New default view in the existing right sidebar (alongside
history/detail/context): **Overview**, fed by one `session_artifacts`
call + live ws events.

* **Outputs** — files this session's `write`/`edit` tool calls touched,
  deduped, newest first. Click → jump into the project workspace with
  that file opened.
* **Subagents** — spawned children (the session DAG already knows them):
  label, status, click → focus that branch.
* **Sources** — files `read` and URLs fetched (`web_search`/`fetch`
  tool calls), deduped.

Server-side this is a scan over the session's persisted tool calls —
no new storage; it's derived data, recomputed on demand and updated
incrementally from the event stream while the session runs.

## 6. Build order

The workspace is built in independently shippable slices, ordered so
the riskiest work lands last and the first slice alone already delivers
the core value — attach a project, browse it, view files in multiple
tabs.

| Slice | Contains | Risk |
|---|---|---|
| **1** | files WS actions + `/files/raw` + `/projects/[id]` with tree + multi-tab read-only viewer | low — all new code, no refactor |
| **2** | chat mounted in the workspace left pane, per-project session tabs + new-session | medium |
| **3** | `/projects` expandable table, `updated_at`, rename | low |
| **4** | chat right-sidebar Overview (outputs/subagents/sources) + transcript file-path links into workspace | low-medium |
| **5** | file management: edit + save (memory-page editor pattern), create/rename/delete, upload/download | medium — write-path safety |

## 7. The tab model

* **Everything is a tab, one project per workspace.** Tab kinds:
  `session` (a chat), `file`, and later `run` (a program/workflow
  execution), all sharing one Tab component and one interaction set.
  The workspace is hard-scoped to a single project; cross-project
  mixing is intentionally impossible.
* **No separate workspace route.** The panes live inside the persistent
  chat surface (AppShell) and slide in/out, so the chat view needs no
  route-singleton decoupling and multi-session is a tab concern rather
  than a session dropdown.
* **Run tabs / workflow visualization**: workflows stay
  plain Python functions (prompts in docstrings, single entry point) —
  no graph DSL. The execution graph is *derived* from the event stream
  the harness already records (`webui/_exec_dag.py`, `graph_builder.py`,
  session DAG renderer), so a run tab is a live view: which node is
  running, what finished, click a node for inputs/outputs. This is the
  deliberate contrast with LangGraph: declare-then-execute vs
  record-first — arbitrary Python control flow becomes a graph with
  zero instrumentation.

## 8. The browser model

There are no layout modes. One mental model covers the whole shell:
**the app is a browser.** The alternatives considered — a stacked side
panel, and a fullscreen workspace mode — both read as cluttered.

* **Center = tab container.** Session tabs and file tabs (later run
  tabs) share one browser-style strip; ＋ opens a new-tab page (new
  session / run agent / project settings). The row under the strip is
  the *active tab's* toolbar — a session tab shows its own project /
  model / thinking / permission settings, a file tab shows breadcrumb +
  view controls. Settings travel with the tab. Session tabs are
  bookmarks over the singleton chat surface (switching a tab drives the
  existing session-switch path), so multi-session costs no chat-engine
  rework.
* **Left sidebar = sessions grouped by project.** The project is the
  group header (name + dim path + per-group new-session ＋); unbound
  sessions fall into a trailing "No project" group. All project/session
  switching lives here.
* **Right sidebar = a plain file tree.** Nothing else resident. The
  Context / Viewport(detail) views only matter when reading the History
  DAG, so History/Context/Executions retreat into an overlay opened
  from a 🕘 button in the session toolbar; legacy DOM mounts and
  `window.rightDock` shims stay alive underneath.
* No "workspace mode": the three-column layout is the only layout, and
  a second content region never has to be carved out — anything new
  becomes a tab, not a pane.

Prototype: `project-workspace-prototype.html`.

## 9. Non-goals

* No embedded terminal, no git panel — the agent does those through chat.
* No CodeMirror/Monaco dependency; editing reuses the textarea
  edit/preview pattern from the memory page until it measurably falls
  short.
* No file watching/live reload of the tree in the first slice; a refresh
  button per directory node suffices until sessions mutate files often
  enough to justify fs-events plumbing.

## Appendix: Implementation Status

Designed, not yet built.

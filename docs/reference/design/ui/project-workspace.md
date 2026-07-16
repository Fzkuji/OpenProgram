# Project workspace — files, tabs, and multi-session

Design record, 2026-07-16. Status: **proposed, not implemented.**

Goal: grow the web UI from "chat with a project chip" into a usable
workspace — browse and view the project's files in multiple tabs, run
several sessions per project, and give the chat page a per-session
overview panel (outputs / subagents / sources). Reference shape: the
three-pane layout used by hosted agent products (chat left, tabbed file
viewer center, file tree right; project list as an expandable table).

## 1. What already exists (reuse, don't rebuild)

| Asset | Where | Reused for |
|---|---|---|
| Project entity layer (id/name/path/sessions, settings.json) | `openprogram/store/project_store.py` | everything |
| Project WS actions (list/create/remove/config/sessions/workdirs) | `openprogram/webui/ws_actions/project.py` | list page, workspace |
| `/projects` page (list + settings/sessions/info tabs) | `web/components/projects/projects-page.tsx` | evolves into the new list page |
| Chat component tree (composer, messages, top-bar) | `web/components/chat/` | workspace left pane |
| Right sidebar shell (history/detail/context views) | `web/components/right-sidebar/` | chat overview panel |
| Memory page editor (edit/preview mode, save) | `web/components/memory/` | file editing (phase 5) |
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

Phase 5 adds `project_file_write`, `project_file_create`,
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
  `/files/raw`, everything else a download card. Read-only in phase 1.
* **Left — chat.** The existing chat view mounted with an explicit
  `sessionId`, plus a session switcher in its header: the project's
  sessions (from `list_project_sessions`) in a dropdown + "new session"
  (created pre-bound to the project via `set_session_project`).
  Multi-session = fast switching within the workspace; the sidebar's
  recents keep working as before.

The chat-view decoupling (route-singleton → `<ChatView sessionId>`)
is the one real refactor in this plan and is why chat lands in phase 2,
after the file panes already work.

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

## 6. Phasing

| Phase | Ships | Risk |
|---|---|---|
| **v1** | files WS actions + `/files/raw` + `/projects/[id]` with tree + multi-tab read-only viewer | low — all new code, no refactor |
| **v2** | chat mounted in the workspace left pane, per-project session switcher + new-session | medium — chat-view decoupling |
| **v3** | `/projects` expandable table, `updated_at`, rename | low |
| **v4** | chat right-sidebar Overview (outputs/subagents/sources) + transcript file-path links into workspace | low-medium |
| **v5** | file management: edit + save (memory-page editor pattern), create/rename/delete, upload/download | medium — write-path safety |

Each phase is independently shippable; v1 alone already delivers the
core ask — attach a project, browse it, view files in multiple tabs.

## 7. Non-goals (for now)

* No embedded terminal, no git panel — the agent does those through chat.
* No CodeMirror/Monaco dependency; editing reuses the textarea
  edit/preview pattern from the memory page until it measurably falls
  short.
* No file watching/live reload of the tree in v1; a refresh button per
  directory node suffices until sessions mutate files often enough to
  justify fs-events plumbing.

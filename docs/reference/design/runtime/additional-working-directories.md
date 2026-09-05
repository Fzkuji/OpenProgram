# Additional working directories for a session

Beyond its main working directory (the bound project path), a session can mount any number of "additional working directories". The semantics match Claude Code's "Add another folder" (`additionalWorkingDirectories`): **an additional directory only widens the permission fence and the model's awareness. It does not change the main cwd, and it does not change where the session is stored.**

The two differ in how freely they change. The main directory freezes on the session's first turn and moves afterwards only to repair a folder that vanished from disk — see [session/operations.md](session/operations.md), "Main project binding". Additional directories are added and removed at any point in the session's life, and each change takes effect from the next turn.

The project path → session cwd link is handled by `project_workdir_for`; the fence side is covered in `docs/reference/design/runtime/permission-model.md` §3.5.

---

## 1. Semantics (what it does and does not do)

| Dimension | Main working directory | Additional working directory |
|---|---|---|
| Model cwd (system prompt, `--cd`, tool ContextVar) | ✅ project path | ❌ unchanged |
| Session repository / artifact storage location | ✅ `<project>/.openprogram/sessions/` | ❌ unchanged |
| acceptEdits fence allowlist (`working_dirs` of `check_path_safety`) | ✅ | ✅ added |
| Announced to the model in the system prompt | ✅ "Current working directory" | ✅ one extra line listing them |
| Storage | project-bound (project_store) | session-level `SessionRunConfig.additional_working_dirs` (session meta, schemaless) |
| Changeable mid-session | ❌ frozen on the first turn (relocate repairs a missing folder) | ✅ added and removed freely |

Out of scope (either Claude Code does not do it either, or this project has no carrier for it):

- CLAUDE.md and project-level settings are not loaded from additional directories. Permission rules still follow the main project only.
- There is no per-directory read-only / read-write grading. The allowlist is binary: once in, a directory is a writable safe zone.
- There are no global (cross-session) additional directories. The carrier is session meta; cross-session needs are served by projects.

Extension points (where to start when these are needed): when entries are upgraded from `str` to attributed objects, only the parsing in `_as_str_list` and the consumer in `check_path_safety` have to change, and schemaless storage means no migration. If MCP roots or multi-root IDE workspaces are wired in, the same field is the single source of truth.

## 2. Data path

`additional_working_dirs` flows from session config all the way to the path fence:

```
UI / ws action
   ↓
SessionRunConfig.additional_working_dirs        session_config.py:61 (field) :79 (load) :127 (save)
   ↓ load_session_run_config
TurnRequest.additional_working_dirs             dispatcher/types.py:112
   ↑ populated by: webui/_execute/chat.py:259, channels/_conversation.py:243
   ↓
_path_is_safe → check_path_safety(path, dirs)   permissions/policy.py → programs/tools/files/file_safety.py
```

`save_session_run_config(..., additional_working_dirs=...)` accepts the parameter; passing `None` means "leave unchanged", so the chat path never accidentally clears existing directories.

## 3. Design, stage by stage

### 3.1 Backend: the fence baseline

The fence's working-directory set is assembled in `openprogram/agent/permissions/policy.py`:

```python
from openprogram.worktree.context import current_worktree_path
work_dirs = [current_worktree_path() or os.getcwd(),
             *getattr(req, "additional_working_dirs", [])]
```

The baseline comes from `current_worktree_path()` — the dispatcher binds the real cwd (worktree or project path) into that ContextVar on every turn, see `dispatcher/__init__.py:387-403` — and the process `getcwd` is only a fallback. This shares a source with the cwd in the system prompt (`_model_tools.py:322`), so the cwd the model is told about and the cwd the fence honors are always the same directory. If they diverge, edits to files inside the project are judged "outside the workspace", acceptEdits does not let them through, and approval prompts fire repeatedly. `worktree.context` depends on stdlib only, so there is no import cycle.

### 3.2 Backend: the `set_working_dirs` ws action

It lives in `openprogram/webui/ws_actions/session.py`, alongside the other session-config actions. The semantics are **whole-list replacement** — the frontend computes the additions and removals and sends the complete list. That is idempotent and removes the "already added / removing something absent" edge cases:

```python
async def handle_set_working_dirs(ws, cmd: dict):
    """Replace the session's additional working directories wholesale. Each dir is
    expanduser'd and must be an existing directory. An invalid entry rejects the whole
    frame (an error frame carries the reason); there are no partial writes."""
    # validation passes → save_session_run_config(session_id, agent_id=..., additional_working_dirs=dirs)
    # → broadcast {"type": "working_dirs", "data": {"session_id", "dirs"}}
```

Validation rule: `Path(d).expanduser()` must satisfy `is_dir()`. What is stored is the expanded absolute path string, not the realpath — the user sees the path they picked, and realpath normalization is left to the `check_path_safety` consumer, which already does it.

### 3.3 Backend: returned in `session_loaded`, carried on the first message

- `data.settings` in `ws_actions/session.py:676-681` includes `additional_working_dirs`, so the frontend restores the list after a refresh or a client switch.
- `handle_chat` in `ws_actions/chat.py`: when `cmd.get("additional_working_dirs")` is not None, it is passed to `save_session_run_config`. This is the only channel through which a draft session (one that has no session_id yet) can persist directories on its first message, and it follows the same pattern as existing fields such as `permission_mode`.

### 3.4 Backend: telling the model in the system prompt

`with_tool_runtime_prompt` takes an optional `additional_working_dirs: list[str] | None = None`, and the dispatcher call site passes `req.additional_working_dirs`. The directories are listed after the "Current working directory" line — the line appears only when the list is non-empty:

```
- Additional working directories (equally writable): /a, /b
```

The two copies in `internals/_model_tools.py` and `agent/_model_tools.py` stay identical, per the "kept in sync" convention stated in the file headers.

### 3.5 Frontend: one chip per directory, right of the project chip

The composer's `envChips` row reads left to right as the session's directory set: `<ProjectBadge />` for the frozen main directory, then `<WorkingDirChips />` — one chip per additional directory plus a trailing icon-only add button (`apps/web/components/chat/top-bar/working-dir-chips.tsx`).

```
[📁 my-project]  [📂 foo ✕]  [📂 bar ✕]  [＋]
```

- The add button opens a picker menu: recent projects (one click mounts that project's path) plus "Choose folder…", which calls `POST /api/pick-folder` (the existing native picker, used on desktop too). The new list is sent with `set_working_dirs` and local state is **updated optimistically** (the instant-feedback principle); when the `working_dirs` broadcast arrives, the backend value wins.
- ✕ on a chip sends the same action with the list minus that entry.
- When the session has no id yet (a draft), only local state updates, and the list rides along on the first chat frame (§3.3).
- Both operations stay available for the whole session — the main directory freezing does not restrict them.

State lives in the session store as `additionalWorkingDirsBySession: Record<string, string[]>` (full words, no abbreviations), fed from three sources: `session_loaded.data.settings`, the `working_dirs` broadcast, and optimistic updates. It does not go into `ComposerSettings` or localStorage, because this is server-persisted session data, not a client-side preference.

The project chip next to them carries the main directory's state, including its warning form. `list_projects` returns `path_missing` per project; when the session's own project has it, the chip switches to the orange warning palette with a lucide `AlertTriangle` in place of the folder icon, and its menu offers "Locate folder…" — the relocate repair described in [session/operations.md](session/operations.md).

### 3.6 Tests

Following the style of the existing files:

- `tests/unit/store/test_session_config.py`: `additional_working_dirs` save/load round-trip, including "None leaves it unchanged" and `_as_str_list` sanitizing.
- `tests/unit/security/test_permission_rules.py`: three `_path_is_safe` cases — allowed inside an additional directory, blocked outside it, and allowed inside the project cwd bound to the ContextVar (monkeypatching `current_worktree_path`).
- `tests/unit/webui/test_ws_working_dirs.py`: the ws action's valid write plus broadcast, whole-frame rejection for a non-directory, and the `session_loaded` round-trip.
- `tests/unit/store/test_session_main_workdir.py`: the main directory's contrasting rules — the freeze, the missing-directory resolution, the relocate record node — plus the case that ties the two together, adding and removing an additional directory on a session whose main directory has already frozen.

## 4. End-to-end flow

```
WorkingDirChips ＋ button
   │ POST /api/pick-folder (native dialog)
   ▼
wsSend set_working_dirs {session_id, dirs}     (draft session → rides on the first chat frame)
   ▼
handle_set_working_dirs: validate → save_session_run_config → broadcast working_dirs
   ▼
session meta (schemaless, no migration)
   ▼ load_session_run_config on every turn
TurnRequest.additional_working_dirs
   ├─→ _path_is_safe: [current_worktree_path() or getcwd(), *dirs] → check_path_safety
   └─→ with_tool_runtime_prompt: system prompt lists the additional directories
```

## 5. Key properties to preserve

These are the design's hard lines; any change has to keep them:

- Additional directories affect **only the fence and the prompt**. Wiring them into cwd switching or storage location violates the semantics table in §1.
- The fence baseline and the system prompt cwd must come from the **same source** (`current_worktree_path()` first). A mismatch produces the "the model thinks it can write, the fence blocks it" approval loop.
- `set_working_dirs` is a whole-list replacement, and a failed validation rejects the entire frame. There is no partially written intermediate state.
- Storage is schemaless (session meta). Old sessions missing the field read back an empty list, with no migration.
- Additional directories stay editable for the session's whole life. The freeze belongs to the main directory alone; extending it to this list would remove the only mid-session way to widen the fence.
- A main project whose folder is missing resolves to `None`, never to another directory. Substituting the default project's home would hand the model a plausible-looking cwd in a place the user never chose.

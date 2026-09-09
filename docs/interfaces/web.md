# Web UI

Selecting a conversation reuses the active session tab, including when another tab already shows that conversation. If a webpage is active, a new session tab opens. Back and Forward at the far left of the tab strip navigate each session tab’s own history. Opening a different conversation after going back replaces the forward history. History survives reloads; drafts retain their input when revisited, and deleted conversations are removed from history. Webpage navigation remains in the webpage controls.

The project menu also reveals its folder in the system file manager, creates a permanent Git worktree, archives its chats, or removes its sidebar entry. Worktrees use a new branch from the current commit and a new absolute folder outside the source Git checkout; uncommitted changes stay in the original folder. Removing a project preserves files, chats, and project ownership. Restore it from the Projects page or reopen its folder. The default home project cannot be removed. Archived chats remain available through the Archived filter; hidden project chats remain in date/flat history.

Right-click a project or use its ellipsis menu to open its settings, start a chat, pin it, edit it, or assign it to a custom section. The shared project editor changes its display name, text icon or emoji, description, and additional source folders. Source folders default into chats that have no explicit folder configuration; removing a folder does not delete files. Main-folder relocation remains in project settings. Section menus rename or remove sections; removing a section keeps its projects.

Project order and chat order are independent. Use the sidebar filter menu to sort projects by newest activity, oldest activity, name, or manual order. New messages update the project activity time automatically. Pin a project using its pin button to keep it above unpinned projects. Chat direction selects oldest-first or newest-first; title sorting also supports A–Z and Z–A. Dragging switches project order to manual. These view preferences are saved locally.

In the sidebar, choose **Group by → Project** to show project folders. Drag a project header above or below another project to reorder it. The insertion line marks the destination. You can also focus a project header and press Alt+Up or Alt+Down. The order is saved on this device and survives reloads; sessions remain in their projects.

See [tool permission modes and live changes](../capabilities/permissions.md) for approval behavior and changes during a task.

The browser interface covers all of OpenProgram's daily operations: chatting, managing functions and programs, configuring providers and MCP, browsing memory and projects. This page walks through each page by route and describes the chat page in detail.

Start it:

```bash
openprogram web
```

Open `http://localhost:18100` in a browser. The page is a static export served by the local FastAPI worker itself — `/api`, `/ws`, and the UI all live on the same single port (18100 by default). All data comes from the worker; sessions are shared with the terminal TUI and CLI one-shots, see the [interfaces overview](README.md). To change the port, use `openprogram ports --port`.

![Chat page](../images/chat_hero.png)

## Chat page (/chat, /s/&lt;session-id&gt;)

`/chat` is the main chat interface; `/s/<session-id>` is a direct link to a single session. Switching sessions does not reload the page, and the WebSocket connection stays open.

### Message streaming

Replies stream in over WebSocket: a placeholder reply appears immediately after sending, and text, thinking, and tool-call blocks render incrementally in arrival order. When several agents write into one session, each assistant message carries the producing agent's avatar and name.

### Messages during a turn

**Queue** keeps messages in order and starts each after the preceding turn finishes. **Steer** and a queued message's **Add to current turn** action append instructions at the running Agent's next safe point. They do not cancel its tool call or change its model, tools or permission settings. Stopping an execution remains a separate composer action.

If an instruction cannot be added to the current turn, its message remains queued. If delivery cannot be confirmed, the row stays visible and retries the same command to avoid duplicate delivery; it does not automatically resend as a new turn. Steering accepts up to 4,096 characters; longer messages remain queued. Unsent queues are stored in the current window's memory and are lost on page reload. Attachments remain in the composer instead of entering the text queue.

### Stopping and reconnecting

Use **Cancel execution** in the composer to stop the current execution. Refreshing or reopening a session restores the active execution and its cancellation controls, including executions resumed after an approval wait. An execution can remain active while no new output arrives; silence alone does not end it. Completed executions do not remain active because of an obsolete worker registration.

Local shell commands stop their process group before reporting cancellation. Resumed conversations persist the original assistant as cancelled and return the session to idle, so reloading preserves the stopped state.

### Collapsible thinking

The model's thinking process renders as a collapsible block, collapsed by default. While streaming, only the latest line shows; click to expand the full content.

### Function-call timeline

Function and tool calls within each reply turn render as an expandable execution timeline: one row per step, with arguments, output, errors, and duration for each function call. Nested calls display recursively as a context tree, and subagents are steps in the timeline too. Clicking a step opens the execution detail panel in the right sidebar. Functions run manually from the `/programs` page's Run dialog use the same timeline rendering. Completed replies retain their streamed timeline; older records without ordered blocks use the same collapsible components instead of a separate tool-call table.

### Attachments

Drag and drop images or text files onto the input box (pasting works too); they are attached to the next message you send.

### Projects on headless or remote Linux

Project folders and additional working directories refer to paths on the
machine running the worker. OpenProgram normally opens that machine's native
folder picker. If Linux has no X11 or Wayland display—for example, a server
reached through SSH—the Web UI opens a manual server-path dialog instead.
Enter an absolute path such as `/srv/projects/example`; the worker verifies
that the directory exists before using it. Cancelling an available native
picker remains a cancellation and does not trigger the manual dialog.

### Session branches and the DAG view

Session history is stored as a DAG, not a flat list:

- The branch menu in the top bar lists all branches of the current session, with checkout, rename, and delete.
- The History view in the right sidebar shows a live mini-DAG of the session: one node per message or function call, colored by branch, with merge and attach operations appearing as nodes of their own. Click a node to collapse or expand its subtree (or jump the chat to that step); double-click a node or edge to check out that branch.
- The Branches panel above the mini-DAG lists branches with a running marker on active ones, and supports multi-select merge — equal merge into a fresh tip, or merge in place into a chosen base branch — as well as attaching branches from another session (cross-session attach).
- Multiple versions of the same message switch via a `< N/M >` selector — it only moves the displayed position, never deletes history.

### Rewind

Each message's action menu has "Rewind to here": it truly rolls the session back to that message, and the undone user input is pre-filled back into the input box for editing and resending. The `/rewind` slash command in the input box is the same feature.

## Other pages

| Route | Purpose |
|---|---|
| `/chats` | History hub: session list (also `/history`); Projects and Memory are tabs on the same page |
| `/programs` | Abilities hub: Programs catalog (call tree / graph). Plugins, Skills, and MCP are sibling tabs |
| `/skills` | Abilities → Skills: browse installed SKILL.md files, discover and create skills; each skill has a detail page |
| `/plugins` | Abilities → Plugins: installed / marketplace / errors |
| `/mcp` | Abilities → MCP: add from the directory, edit configs, view per-server status |
| `/memory` | History → Memory: wiki, journal, and core memories |
| `/projects` | History → Projects: per-project permission rules, default settings, associated sessions |
| `/settings` | Settings: providers (models and credentials), search, general (including theme, runtime version, and Desktop update status when the Electron bridge is present), system, usage, auth, channels |

Opening `/settings` directly lands on `/settings/general`. Model credentials stay on `/settings/providers`; see [configuring models](../models/README.md).

### Conversation activity

**Activity** replaces the separate Running and Debugger entries. It follows the selected conversation only when its tab, visible route and chat state agree; new tabs, unsent drafts and non-conversation pages show an empty prompt instead of the previous conversation. Opening a Program from Abilities starts a draft tab; opening its parameters does not create execution history. Activity counts conversation branches, not messages or execution attempts. Consecutive messages on one branch remain one row; a real fork creates a separate row. Select a branch to inspect its individual executions, called sub-agents and managed background programs. Programs stay under the execution that started them. A called Agent may have a separate execution conversation; only the explicitly linked execution and its descendants are included, not every task in that conversation.

**Needs attention** groups paused tasks and unconfirmed external actions. Ended attempts whose only missing receipts are model responses appear in **History** as “Ended · response record incomplete”; they do not ask for confirmation. This also covers an unfinished cancellation whose execution owner has already ended. Cancel is not offered for these ended records unless they have an active child. Actual questions, approvals, unresolved tool actions and active child tasks remain visible. **In progress** includes running tasks and completed Agents whose programs are still active. **History** is collapsed by default and counts ended conversation branches. A branch moves between sections as its work changes; past turns do not create additional History rows. Its ordinary status follows the latest execution, while actual outstanding waits and active programs remain visible. Branch details retain all execution records, their start times, sub-Agents and programs. Records whose branch cannot yet be established stay in a separate uncounted group. Longer request excerpts can be expanded in task details. Program rows identify recognized script files, Python modules, or package scripts and retain a per-owner number. Inline code is labelled as a snippet; commands that cannot be classified conservatively retain their executable name. The complete original command remains available in program details. Finishing an Agent does not remove its programs. Refreshing the page or restarting the local worker preserves managed process records and output. Old processes that were never recorded cannot be recovered or assigned retroactively.

Select an Agent to inspect its progress and use the existing execution controls. Pause, Continue, Step and Retry appear only when supported by that execution. Use **Add instruction** or **Create branch** to open the shared editor dialog. Published instructions remain read-only; **Edit as new draft** creates a separately validated revision without changing the published version. Outstanding questions and approvals appear before progress history. Internal IDs, revision data, resource snapshots, effect counters, checkpoints and raw events are under **Technical details**. A result awaiting confirmation is a blocked execution, not ongoing generation. When it has no active attempt, the composer permits a new message and does not restore Cancel after refresh; Activity retains the unresolved result and its restrictions; provider-only missing response records remain in History. A fetched snapshot does not prove an Agent is currently running.

Select a program to see its command, working directory, environment, start and end times, exit code and recorded output. **Stop program** targets that program's managed process group, including ordinary background descendants. Other programs are unaffected. Output is bounded; the view explicitly indicates truncation while retaining the process record. Activity updates automatically on execution and job events, when the panel opens, and after connection or page visibility recovers. It has no manual refresh button. Execution snapshots have a 30-second fallback refresh; managed programs and their output are checked every 3 seconds while visible because they do not yet publish a dedicated event stream. An initial read failure displays a loading error rather than claiming older records exist. Programs without an Agent record remain visible and prevent an empty-activity message. A failed refresh keeps the last records, selected program details and output visible with a stale-state notice. Changing the selected program clears the previous details; a successful refresh that no longer includes that program also clears them.

Long-running programs should be launched through the `process` tool's `start` action. The launch belongs to the trusted execution and session context. Its detached supervisor retains output and control across worker restarts; ordinary shell background children remain part of the managed group. Programs deliberately detached into another process session, or started outside framework process management, are not claimed as monitored.

To branch an Agent from a saved point, enter instructions in **Branch with new instructions**, then prepare, check, confirm and publish the revision. **Create branch** selects a separate paused child; **Continue** starts it with the published instructions. Its messages and checkpoints are independent of the original execution. Editing unpublished instructions creates a new draft version and invalidates the previous validation and approval. Instruction-only revisions preserve program and runtime contracts; sensitive program, tool, model or output changes retain independent approval.

Retry creates an independent paused Agent execution at the saved point with the original revision. Select **Continue** to resume it.

## Manage project files

Files uses a path row, an action row, and an optional search row. Click a path segment to locate its folder in the project tree. The action row contains Search, New File, New Folder, Refresh, Sort and display, and Get Info. Sort each directory by name, modified time, size, or file type; choose ascending or descending order, folders first, and whether hidden or Git-ignored entries appear. Settings are saved locally per project and shared by the sidebar and central Files view. Search keeps relevance ordering.

Get Info is available in the toolbar and file context menu. It shows paths, file bytes, timestamps, and read-only permissions. Visible folders are scanned in a bounded queue. Folder rows show approximate complete or cached sizes; `≥` marks a partial result. Details show the scan time, skipped entries, and controls to continue, cancel, or recalculate. Scans sum logical file bytes, include hidden files, and skip symbolic links, restricted directories, and unreadable entries. They are samples taken over time, not atomic snapshots or disk allocation measurements. Cached values may be outdated. Complete recent samples participate in size sorting; refresh to apply newly calculated sizes. Unknown and partial totals stay last in their group.

The path uses larger text, and its right-hand copy button copies the full absolute path. Refresh keeps the selected path, expanded folders, and loaded page range. File rows show readable sizes, including empty files; hover over a size for its exact byte count.

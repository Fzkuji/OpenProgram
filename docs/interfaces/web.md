# Web UI

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

### Stopping and reconnecting

Use **Cancel execution** in the composer to stop the current execution. Refreshing or reopening a session restores the active execution and its cancellation controls, including executions resumed after an approval wait. An execution can remain active while no new output arrives; silence alone does not end it. Completed executions do not remain active because of an obsolete worker registration.

Local shell commands stop their process group before reporting cancellation. Resumed conversations persist the original assistant as cancelled and return the session to idle, so reloading preserves the stopped state.

### Collapsible thinking

The model's thinking process renders as a collapsible block, collapsed by default. While streaming, only the latest line shows; click to expand the full content.

### Function-call timeline

Function and tool calls within each reply turn render as an expandable execution timeline: one row per step, with arguments, output, errors, and duration for each function call. Nested calls display recursively as a context tree, and subagents are steps in the timeline too. Clicking a step opens the execution detail panel in the right sidebar. Functions run manually from the `/programs` page's Run dialog use the same timeline rendering.

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

**Activity** replaces the separate Running and Debugger entries. It shows the current conversation's Agent executions, called sub-agents, branches, and managed background programs. Programs stay under the execution that started them. A called Agent may have a separate execution conversation; only the explicitly linked execution and its descendants are included, not every task in that conversation.

**Needs attention** groups paused tasks and unconfirmed outcomes. **In progress** includes running tasks and completed Agents whose programs are still active. **History** is collapsed by default and retains ended task trees. Expand a task to see its sub-Agents and programs; select a name to inspect details. Task rows use the original request excerpt, while program rows use a short executable name and number. Finishing an Agent does not remove its programs. Refreshing the page or restarting the local worker preserves managed process records and output. Old processes that were never recorded cannot be recovered or assigned retroactively.

Select an Agent to inspect its progress and use the existing execution controls. Pause, Continue, Step and Retry appear only when supported by that execution. Internal IDs, revision data, checkpoints and raw events are under **Technical details**. A result awaiting confirmation is a blocked execution, not ongoing generation. When it has no active attempt, the composer permits a new message and does not restore Cancel after refresh; Activity retains the unresolved result and its restrictions. A fetched snapshot does not prove an Agent is currently running.

Select a program to see its command, working directory, environment, start and end times, exit code and recorded output. **Stop program** targets that program's managed process group, including ordinary background descendants. Other programs are unaffected. Output is bounded; the view explicitly indicates truncation while retaining the process record. A failed refresh keeps the last records visible with a stale-state notice.

Long-running programs should be launched through the `process` tool's `start` action. The launch belongs to the trusted execution and session context. Its detached supervisor retains output and control across worker restarts; ordinary shell background children remain part of the managed group. Programs deliberately detached into another process session, or started outside framework process management, are not claimed as monitored.

To branch an Agent from a saved point, enter instructions in **Branch with new instructions**, then prepare, check, confirm and publish the revision. **Create branch** selects a separate paused child; **Continue** starts it with the published instructions. Its messages and checkpoints are independent of the original execution. Editing unpublished instructions creates a new draft version and invalidates the previous validation and approval. Instruction-only revisions preserve program and runtime contracts; sensitive program, tool, model or output changes retain independent approval.

Retry creates an independent paused Agent execution at the saved point with the original revision. Select **Continue** to resume it.

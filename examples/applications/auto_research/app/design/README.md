# Auto Research design

The usage flow and screens agreed with the author. The first canvas
(https://claude.ai/artifact/PhRELR6wmcddj8K5F9PMQi) fixed the welcome page, the
workspace and the task list; its artboards are kept here. The task page was later
redrawn around a conversation (layout "A"): the task's files above, the last
exchange under them, OpenProgram's composer at the bottom, no right sidebar.

| File | What it shows |
| --- | --- |
| `Main.dc.html` | The flow: open the app, enter a project, pick a task. It also lists what is always present. |
| `Welcome.dc.html` | No project open: open a folder (system chooser, ⌘O) or pick from OpenProgram, and the recent projects. A folder that is not a repository gets a notice in place. |
| `Workspace.dc.html` | An open project: the project switcher, tasks by phase with their state and the status bar. |
| `Task.dc.html` | The first task page; superseded by layout A (see `../README.md`). |
| `canvas.json` | The layout of the canvas. |

The `.dc.html` files are Design canvas artboards. They render on the canvas, not
when opened directly in a browser. Screens are mock-ups: bracketed text such as
`[分支]` stands for live data.

What an author needs from a task page, which the page keeps:
- See what the task has made: the figure, the PDF page, the claims, the table, without
  leaving the app.
- Know what is still missing: the last check's findings, on top, short.
- Say what to change in words, to an agent that already knows the task and works in
  the paper's folder; see what it changed as soon as it ends.
- Come back later to the same conversation: one per task.
- Decide: the agent never approves anything; the author runs the check and approves.
- A blocked task says why and links to the task that unblocks it.
- The page is built from OpenProgram's own component kit (see `../README.md`).

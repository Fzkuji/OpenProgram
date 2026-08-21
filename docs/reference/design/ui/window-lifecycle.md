# Desktop window lifecycle

The desktop shell is one process and **one main window**. Torn-off
windows are extra and short-lived. Opening the app, clicking the Dock
icon, or launching a second copy must not create a second main window.

Related: [`window-state.md`](window-state.md) (bounds and chrome, not
count). Code: `apps/desktop/window-lifecycle.js`, `apps/desktop/main.js`.

## What went wrong

Three paths all called `createWindow()`:

1. `app.whenReady()` — first launch.
2. `app.on("activate")` — macOS Dock click, including during launch.
3. `app.on("second-instance")` — a second click while the first process
   is still starting.

`createWindow` is async: it builds the `BrowserWindow`, then awaits the
worker URL. Until that first object is in `BrowserWindow.getAllWindows()`,
paths 2 and 3 see an empty list and create another window with id
`main`. The map keeps only the last one. The user sees two identical
app windows.

A background LaunchAgent (`ai.openprogram.worker`) keeps the worker on
port 18100. That is not a second app window. If the worker is slow, the
main window may flash the error page and then reload — still one window.

## Rule

| Event | Action |
|---|---|
| Ready, no live main | Create one main window. |
| Ready / activate / second-instance, create already in flight | Join that same create. Do not start another. |
| Live main already exists | Reuse it. Restore, show, and focus. |
| Main was destroyed | Create one new main. |
| Second OS process | Quit. The first process handles the click. |
| Torn-off / detached window | Not a main. Own id. Does not persist bounds. |

`windows.get("main")` is the live main. `ensureMainWindow()` is the only
entry that creates or reuses it. `createWindow({ detached: true })`
stays on the tear-off path.

## Implementation

`createMainWindowGate({ windows, createWindow })` returns
`ensureMainWindow`. It holds at most one in-flight promise.

`registerSingleMainWindow({ app, BrowserWindow, ensureMainWindow, recoverErroredWindows, onReady })`
takes the single-instance lock, then wires the three events to that
gate. `onReady` is IPC, menu, and theme setup. It runs once, before
the first `ensureMainWindow()`.

Harness: `apps/desktop/scripts/check-window-lifecycle.js`.

## Out of scope

Worker start, `ui.open_browser`, and packing the `.app` do not create
the main `BrowserWindow`. Do not open the system browser to
`127.0.0.1:18100` when the desktop shell is the UI.

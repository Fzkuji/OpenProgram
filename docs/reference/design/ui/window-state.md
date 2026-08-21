# Desktop window state

The desktop main window remembers its last *normal* size separately from
maximize / fullscreen chrome. Restoring a previous session never creates a
normal window whose edges sit flush with the menu bar, Dock, or screen
sides — that is the “stuck, cannot resize” class of bugs.

Related code: `apps/desktop/window-state.js`, `apps/desktop/main.js`,
`apps/web/app/styles/base.css`, `apps/web/components/app-shell.tsx`.
How many main windows exist, and who creates them, is
[`window-lifecycle.md`](window-lifecycle.md).

## Why a flush normal window cannot be resized

Electron’s `BrowserWindow` is resizable. Persistence used to write only
`{x,y,width,height}` from `win.getBounds()` to
`app.getPath("userData")/window-state.json` (desktop name
`openprogram-desktop`). Zoom or a drag that fills the display work area,
then quit, saved those exact pixels as a *normal* window.

On a 1512-wide logical display that is `{x:0,y:38,width:1512,height:851}`:
edges sit on the menu bar, Dock, and screen sides, so there is nothing to
grab. The green Zoom button also has no remembered restore size.
`-webkit-app-region: drag` on the 40px desktop tab row used to run
edge-to-edge, which also swallowed the top-edge and corner resize hit
targets.

Setting `resizable: true` or deleting the state file does not address this.
The stored model has to distinguish chrome from normal bounds.

## Persisted schema

The file is `window-state.json` under the desktop userData directory.
Version 2 is the current shape. A version-1 file is the legacy
`{x,y,width,height}` object and migrates on load.

```json
{
  "version": 2,
  "x": 160,
  "y": 90,
  "width": 1280,
  "height": 800,
  "isMaximized": false,
  "isFullScreen": false,
  "displayId": 1,
  "displayWorkArea": { "x": 0, "y": 38, "width": 1512, "height": 851 }
}
```

| Field | Role |
|---|---|
| `x,y,width,height` | Last *normal* bounds. Never a work-area-filling rectangle. |
| `isMaximized` | Last Zoom / maximize chrome. |
| `isFullScreen` | Last macOS native fullscreen chrome. |
| `displayId` | Electron display id, used first when that screen is still connected. |
| `displayWorkArea` | Work-area rectangle so a reconnect that changed `id` still matches. |

Minimum usable size is `800×500`. There is no maximum size. First-run
default size remains `1440×900`, centered on the primary work area, then
inset if that default would itself fill the work area.

## Restore algorithm

1. Read and migrate the file. Corrupt JSON, missing width/height, `NaN`,
   or a postage-stamp size fall through to the centered default.
2. Resolve a live display: matching `displayId`, then a work-area match
   (2px tolerance), then the legacy “bounds still overlap a work area”
   check. If none match — unplugged external monitor — use the primary
   work area and the centered default. Maximized / fullscreen flags are
   dropped on that fallback so the window opens usable.
3. If the saved rectangle fills a connected work area (legacy files),
   treat it as maximized and replace the normal size with the inset
   default. The `BrowserWindow` is never constructed at work-area size.
4. Clamp the normal rectangle onto the chosen work area.
5. Create the window at that normal size with `minWidth` / `minHeight`
   set. Then call `maximize()` and/or `setFullScreen(true)` when those
   flags were saved.

Green Zoom therefore restores to the remembered normal size, not to a
flush rectangle.

## Maximize versus a filled work area

If the user stretches the window to the work area without using Zoom, the
session is persisted as maximized:

- `isMaximized` is set.
- The last *non-filling* normal bounds are kept. Electron
  `getNormalBounds()` is used when it is itself non-filling; otherwise
  the in-memory last-good rectangle, then the inset default.
- Restore opens maximized. Zoom returns to that smaller normal size.

A rectangle is “filling” when it matches the display work area within
8 CSS pixels. The rule is applied on both save and restore so an old
`{x:0,y:38,width:1512,height:851}` file cannot come back as a flush
normal window.

## Save timing

The main window writes on `resize`, `move`, `maximize`, `unmaximize`,
`enter-full-screen`, `leave-full-screen`, and `close`. Move and resize
are debounced (300ms) so a drag does not flood the file. Chrome events
and close flush immediately.

While the window is maximized, fullscreen, or work-area-filling, only
the flags and display identity update. The last good normal rectangle is
left alone.

## Display fallback

Unplugging the saved display, or bounds that are off-screen, `NaN`, or
smaller than the minimum, opens a centered default on the primary work
area. The default is inset when `1440×900` would fill that work area, so
the fallback is always grabable.

## Detached windows

Torn-off windows stay ephemeral. They use a modest size
(`min(1100, normal width)` × `min(720, normal height)`), omit the
parent’s `x/y`, and never inherit maximize or fullscreen. Placement still
happens at the drop point through `centerHiddenWindowOnCursor`. Closing a
detached window does not write `window-state.json`.

## Titlebar hit-testing

The 40px desktop tab row remains `-webkit-app-region: drag` so empty
chrome moves the window. Three 5px `no-drag` strips sit on the top, left,
and right edges (and therefore the corners) so macOS can hit-test native
resize. Traffic lights start at `{x:18,y:13}`; tabs sit 6px below the
top. The inset does not cover those targets, the `+` button, or the
main-menu button (those already mark `no-drag`).

## How this prevents the “cannot resize” class of bugs

| Failure | Guard |
|---|---|
| Quit while Zoomed or work-area-filling | Restore creates a smaller normal window, then reapplies chrome. |
| Legacy flush `{x,y,width,height}` file | Migrated as maximized + inset default. |
| Unplugged display | Centered default on the primary work area. |
| Corrupt / tiny file | Minimum size + centered default. |
| Tab row swallows the top edge | 5px `no-drag` insets. |
| Closing a torn-off window | Detached windows do not persist. |

## Implementation status

Implemented: schema v2, legacy migration, restore-then-chrome sequence,
filled-work-area → maximized rule, display fallback, main-window
debounced persistence, detached ephemeral sizing, titlebar resize
insets, and the `apps/desktop/scripts/check-window-state.js` harness.

Desktop chrome color follows the resolved web theme (`theme-chrome.js`);
that is specified on [unification-work.md](unification-work.md). Window
bounds persistence does not store or restore a background color.

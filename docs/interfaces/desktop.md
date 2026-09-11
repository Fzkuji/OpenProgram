# Desktop App and built-in browser

The macOS and Windows Desktop Apps present OpenProgram as a multi-pane workspace. Each pane can hold Files, a chat, the built-in Browser, or a Terminal, and panes can be split or moved between app windows without changing the underlying session or browser tab.

On macOS, install the architecture-matched DMG and copy `OpenProgram.app` to `/Applications`; the current macOS channel is unsigned and may require **System Settings → Privacy & Security → Open Anyway**. On Windows, install only the signed `win-x64.exe` or `win-arm64.exe` attached to a published [GitHub Release](https://github.com/Fzkuji/OpenProgram/releases). If a release has no signed Windows EXE, use the CLI/server and browser UI for that version. The complete steps are in [Installation](../install/install.md).

Terminal panes use the login shell on macOS and Windows PowerShell through ConPTY on Windows. Packaged apps on both platforms start the worker from their embedded managed Python and do not depend on a system Python or Node.js.

When running Desktop from source on Linux, terminal panes use an installed absolute `SHELL` path, then fall back to `/bin/bash` or `/bin/sh`. They do not require zsh. If no shell is available, the terminal reports the missing prerequisite before starting a process.

## Opening the Browser

Create a pane and select **Browser**, or open a new browser tab from the app tab bar. The Browser home shows the same browser chrome used by loaded webpages:

- Back, Forward, Reload/Stop, Home, address/search field, bookmark-current-page, Bookmarks, open externally, and the Browser menu.
- A bookmarks bar that is visible by default and can be hidden from the Browser menu or Browser settings.
- Responsive controls: less frequent actions move into the Browser menu when the pane is narrow; the address field, Back, Reload/Stop, bookmark-current-page, and menu remain available.

The Browser menu owns browser-specific actions: new browser tab, Bookmarks, History, bookmarks-bar visibility, profile import, clear browsing data, and Browser settings. Window and pane actions remain in the OpenProgram window menu.

## Page navigation, popups, and right-click actions

The webpage decides whether an action navigates its current page or requests a new browsing context. Ordinary links, form submissions, and same-page navigation stay in the current Browser tab. Links with `target="_blank"` and scripts that call `window.open()` create a distinct Browser tab and activate it immediately.

Right-click a link inside a webpage to open it in a new Browser tab or copy its address. A page context also provides Back, Forward, and Reload; editable fields provide Undo, Redo, Cut, Copy, Paste, and Select All when the webpage reports that each action is available. These actions apply only to the exact Browser tab that opened the menu.

## Bookmarks and History

The bookmarks bar shows the direct contents of the imported or locally maintained Bookmarks bar. Non-empty Other bookmarks and Mobile bookmarks folders remain separate folder entries. Long rows use a bounded overflow menu; nested folders open one level at a time and remain scrollable within the current window.

The Bookmarks manager has a folder tree, current-folder list, search, favicon display, and item menus. History is grouped by local date and uses compact rows with time, favicon, title, and domain. Desktop Browser data is separate from backend state: History and the persistent `webtabs` partition live in Electron's per-user application-data directory, while chats, projects, Programs, and worker configuration remain under `~/.openprogram/`. Clearing browser data does not delete that backend state.

## Importing an existing browser profile

On macOS and Windows, OpenProgram can discover local Google Chrome, Brave, Microsoft Edge, and Chromium profiles. Import is always explicit: choose the source browser, profile, and any of the supported data types.

| Data | Behavior |
|---|---|
| History | Copies up to the supported limit of HTTP/HTTPS visits and merges them into OpenProgram History |
| Bookmarks | Preserves the bookmarks-bar, other-bookmarks, mobile-bookmarks, and nested-folder structure while filtering invalid URLs and duplicates |
| Cookies | Uses a temporary source-browser process to decrypt eligible cookies, validates them, and writes them through Electron's cookie API; some sites still require a new login |

OpenProgram does not import passwords, payment or address autofill data, downloads, cache, localStorage, Service Workers, browser extensions, or extension storage. It does not modify the source profile.

## Agent access to a split Browser pane

When a chat turn has a visible built-in Browser pane in the same app window, OpenProgram attaches a bounded description of that exact WebTab to the turn before the first model response. The Agent receives the page title, origin, visible text, ARIA landmarks, and a browser-control tool. This works whether the Browser pane is on the left or right, in a picture-in-picture preview over chat, and does not require the app window or Browser pane to have operating-system focus.

If the Agent opens a page while you stay in chat, Desktop shows that live WebTab as a small corner preview. The preview can expand into a chat-and-page split, take over the center pane, or close without destroying the tab. Closing the preview only hides it; the page remains available from **Session resources** in the right sidebar. The web UI (a browser tab, not the Desktop App) has no native BrowserView, so the same preview falls back to an iframe or an Open-in-new-tab control.

Actions remain bound to the originating window and WebTab. The default path uses DOM, ARIA, page text, and element references. A single current-viewport screenshot is used only for a visual task or when the page cannot be located structurally. The product does not add OCR, object detection, iterative crops, component memory, vision memory, or workflow replay to this path.

Each preview belongs to its conversation, even when you navigate between conversations within the same chat tab. Switching conversations hides the previous preview and restores the selected conversation’s preview. Pages retained only in a conversation are invisible and inaccessible to other Agents. **Open page** exposes that same Page as a regular tab, where another conversation can discover it and acquire control after the current Agent releases its exclusive control. Opening a tab does not transfer the original conversation’s preview.

The preview header shows Auto preview or Fixed preview. Auto preview tracks the page the Agent is operating. Pin preview holds the current page; Unpin preview returns to automatic display of that Agent-operated page. This selection does not change the Agent’s target, resume or start the Agent, or change the page’s lifetime or top-tab placement. Session resources has no separate pin control. The default chat preview is 300×198.75 (300×168.75 image plus the header). Drag the header to move it; resize from any of the four edges or four corners. The overlay keeps the image aspect ratio (16:9 by default). There is no visible corner grip; the pointer changes at the outer border. Resizing the preview does not resize or zoom the underlying page. In Resources, each group heading is a full-row control with a trailing chevron matching the left sidebar project rows.

## Browser extensions

Chrome Web Store and Edge Add-ons pages open as ordinary webpages, but OpenProgram does not install browser extensions, download CRX packages, import extensions from another browser, or provide an extension manager. The app uses standard Electron/Chromium. Electron exposes only part of the Chrome Extensions API and explicitly does not target compatibility with arbitrary Chrome Web Store extensions; OpenProgram does not maintain a custom Chromium/Electron fork or add another browser runtime for extension compatibility. The Playwright Chromium shipped with the complete runtime belongs to the browser automation backend; it does not host the Desktop Browser Pane or extensions.

Use [OpenProgram Plugins](../capabilities/plugins.md), Skills, MCP servers, Programs, or agent tools to extend OpenProgram itself. These do not modify the embedded webpage runtime.

The maintained engineering specifications are [Built-in browser design](../reference/design/ui/built-in-browser.html) and the [Web Use / Computer Use boundary](../reference/design/integrations/web-use.html).

## Session resources

Click **Resources** in the right sidebar to open **Session resources**. Use the sidebar toggle to collapse or expand the panel. The panel shows only resources owned by the selected session. There is no search field. Switching sessions updates the listed resources. The panel lists complete software/environment objects: webpages, VM or desktop attachments, and actual container or remote-environment objects registered by integrations. Code executions, commands, scripts, background processes and output belong in Activity. File and terminal views are not listed as software resources. Views without recorded session ownership and new draft chats show no session resources. Opening an owned resource keeps its session context.

Agent-created webpages with recorded session ownership stay out of the top strip unless pinned or in a split. Legacy pages without an owner remain in the top strip. Select a webpage to open its existing view. Webpages can be pinned or closed; collapsing the panel or a group leaves resources running. Selecting a resource keeps the panel open beside its view.

Docker and SSH commands stay in the execution/Activity flow; running a command does not create a software resource. GUI Harness reports its configured desktop or VM attachment. Other integrations must register the actual software/environment object and its identity, rather than a command, image name or process. Select an environment resource to inspect its target and status.

Integrations can report complete software/environment objects through `openprogram.session_resources.resource_use(kind, title, target)` inside a trusted runtime session. The context records the actual session automatically and releases the usage on exit. OpenProgram does not infer resources from arbitrary shell command text. URL resource identities omit credentials, query parameters, and fragments.

## Restart and retained browser pages

After the Desktop App or worker process restarts, pages you had not closed come back automatically in the background. The App keeps the same retained tab, session, and branch grouping, and the last confirmed address and title. It creates a new live page behind that retained tab; handles from the previous process are no longer valid. It does not open a second copy of the same retained tab, does not reopen a page you closed, and does not show a preview you had hidden. Compact picture-in-picture chrome, the vertical Files / Activity / Resources sidebar, and grouped resource rows stay as they were. Labels follow the App language setting.

While a page is coming back, its row stays in the original branch and shows **Restoring page…**. If restore fails, the same row shows **Could not restore page**. If the new live page later needs a new connection, the row shows **Reconnect**. Those rows do not move into a generic Unavailable group. Only pages you explicitly closed appear under **Closed pages**. Restoring a page does not start or continue an Agent. Idle pages stay ready to use. A new task still observes the page and checks permission as usual. Restore reloads the last confirmed URL with site storage already in the Desktop `webtabs` partition; unsaved DOM and form fields from the previous process are not restored. An older resource record with no retained tab descriptor is not recreated automatically; that row stays in its original group as **Could not restore page**. This bounded restart behavior is implemented in the default App.

Conversation history loads the latest page first. Use **Load earlier messages** above the transcript to retrieve older pages without losing new streamed output. Pages follow the same conversation branch. Reconnecting reloads the recent page; stored history and model context are unchanged.

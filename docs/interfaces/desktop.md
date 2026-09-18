# Desktop App and built-in browser

Settings are an application-wide page. Opening Settings from the account menu, app menu, keyboard shortcut or Browser menu leaves every tab and its Back/Forward history unchanged. Select a tab to return to its content. Settings sections can still be opened directly by URL; older settings entries are removed when saved tabs are restored.


Local App refresh preserves permissions of unpacked native dependencies and restores execute permission on the node-pty terminal helper when an earlier refresh removed it.

Each tab keeps its own Back and Forward history, beginning with its default launcher. Navigation restores pages only inside the selected tab; switching tabs adds no history. Opening the same destination from another launcher preserves both tabs independently. Choosing a new destination after Back replaces only that tab’s forward history. History is saved with the tab. Browser controls handle navigation between webpage URLs.

The macOS and Windows Desktop Apps present OpenProgram as a multi-pane workspace. Each pane can hold Files, a chat, the built-in Browser, or a Terminal, and panes can be split or moved between app windows without changing the underlying session or browser tab.

Closing the last tab leaves both the tab bar and center content area empty. No tab or launcher is created automatically. Use the plus button to open a new tab.

The Back and Forward buttons also include the initial New tab page. After opening an application or built-in page from it, use Back to return to the initial page and Forward to restore the destination in the same tab. Choosing another destination after going back replaces the forward history.

On macOS, install the architecture-matched DMG and copy `OpenProgram.app` to `/Applications`; current macOS releases are Developer ID signed and Apple notarized. On Windows, install only the signed `win-x64.exe` or `win-arm64.exe` attached to a published [GitHub Release](https://github.com/Fzkuji/OpenProgram/releases). If a release has no signed Windows EXE, use the CLI/server and browser UI for that version. The complete steps are in [Installation](../install/install.md).

Terminal panes use the login shell on macOS and Windows PowerShell through ConPTY on Windows. Packaged apps on both platforms start the worker from their embedded managed Python and do not depend on a system Python or Node.js.

When running Desktop from source on Linux, terminal panes use an installed absolute `SHELL` path, then fall back to `/bin/bash` or `/bin/sh`. They do not require zsh. If no shell is available, the terminal reports the missing prerequisite before starting a process.

Opening a conversation from a session tab reuses that tab, even when another tab displays the same conversation. A launcher also opens its own independent instance. From other page kinds, an existing session tab can be selected or a new one opened. Back and Forward always stay in the selected tab. Draft input and background title updates survive navigation and reload; deleted sessions are removed from every history.

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

Click **Resources** in the right sidebar to open **Session resources**. Use the sidebar toggle to collapse or expand the panel. The panel shows only resources owned by the selected session. There is no search field. Switching sessions updates the listed resources. The panel lists complete software/environment objects: webpages, VM or desktop attachments, and actual container or remote-environment objects registered by integrations. Code executions, commands, scripts, background processes and output belong in Activity. File views remain separate. Persistent Terminal environments appear in this same resource list after they are associated with the session. Views without recorded session ownership and new draft chats show no session resources. Opening an owned resource keeps its session context.

Agent-created webpages with recorded session ownership stay out of the top strip unless pinned or in a split. Legacy pages without an owner remain in the top strip. Select a webpage to open its existing view. Webpages can be pinned or closed; collapsing the panel or a group leaves resources running. Selecting a resource keeps the panel open beside its view. Closing a webpage removes its active row immediately while the close request is processed. If closure is not confirmed, the row becomes available again.

Docker and SSH commands stay in the execution/Activity flow; running a command does not create a software resource. GUI Harness reports its configured desktop or VM attachment. Other integrations must register the actual software/environment object and its identity, rather than a command, image name or process. Select an environment resource to inspect its target and status.

Integrations can report complete software/environment objects through `openprogram.session_resources.resource_use(kind, title, target)` inside a trusted runtime session. The context records the actual session automatically and releases the usage on exit. OpenProgram does not infer resources from arbitrary shell command text. URL resource identities omit credentials, query parameters, and fragments.

## Running conversations after restart

Running conversations continue automatically in the same conversation when the worker restarts within the configured recovery window (two hours by default), using the input and execution results recorded during normal operation. No separate save action or additional restart message is needed. Manually paused, cancelled, and completed tasks remain stopped. Confirmed tool results are reused; an operation whose external result is still unknown requires reconciliation before it can continue. Reconnecting the interface keeps the existing transcript.


Conversations show normal messages and tool results, including results of an update you requested. They do not contain a separate software-update history, update controls, or update recovery notices. Opening a conversation does not start software-update history polling.

## Restart and retained browser pages

After the Desktop App or worker process restarts, pages you had not closed come back automatically in the background. The App keeps the same retained tab, session, and branch grouping, and the last confirmed address and title. It creates a new live page behind that retained tab; handles from the previous process are no longer valid. It does not open a second copy of the same retained tab, does not reopen a page you closed, and does not show a preview you had hidden. Compact picture-in-picture chrome, the vertical Files / Activity / Resources sidebar, and grouped resource rows stay as they were. Labels follow the App language setting.

While a page is coming back, its row stays in the original branch and shows **Restoring page…**. If restore fails, the same row shows **Could not restore page**. If the new live page later needs a new connection, the row shows **Reconnect**. Those rows do not move into a generic Unavailable group. Explicitly closed pages and terminated terminals appear under **Closed resources**. Restoring a page does not start or continue an Agent. Idle pages stay ready to use. A new task still observes the page and checks permission as usual. Restore reloads the last confirmed URL with site storage already in the Desktop `webtabs` partition; unsaved DOM and form fields from the previous process are not restored. An older resource record with no retained tab descriptor is not recreated automatically; that row stays in its original group as **Could not restore page**. This bounded restart behavior is implemented in the default App.


## Browser interaction and pause

You may click, scroll, type, navigate, or close built-in pages while an Agent works. These actions do not pause the Agent. Agents may also operate OpenProgram's own web interface. Use the task pause control next to the conversation composer to pause execution.

When a task page disappears, the Agent reacquires the page or reopens its last known address if it has closed, then reads its current state. Previous clicks and submissions are not replayed automatically. Lost authentication or unsaved content is reported when it cannot be restored.

Conversation history loads the latest page first. Scrolling retrieves nearby messages automatically in either direction while keeping your reading position and new streamed output. No loading button is needed; failed requests retry automatically. Distant pages leave the memory cache and are retrieved again when you return. Reopening a conversation restores the visible message when it still exists on the selected branch. Jump to latest retrieves the latest page directly. The DAG is loaded when you open its view; ordinary Chat does not calculate its drawing coordinates. Rendered Markdown uses a bounded cache, so previously viewed long content does not accumulate without limit. Stored history and model context are unchanged.

The main conversation and split panes each provide Jump to latest while newer messages remain outside the loaded window. A failed jump keeps the control available for retry. Scrolling, clicking inside the transcript, or pressing a key there cancels a pending jump or saved-position restoration; late replies do not replace the window you are reading. Closing a pane saves its final reading position. Reconnecting resumes automatic history loading without waiting for an old retry delay.

## Continuing after a restart

Closing a conversation tab or the App window leaves worker-owned tasks running. If the worker itself stops, resumable Agent tasks save checkpoints at completed provider and tool boundaries. On restart, tasks paused by shutdown or recovered from a safe abandoned checkpoint automatically continue within two hours. The deadline is persisted; another restart does not extend it. After expiry, the task stays paused and can be continued manually.

The setting `execution.auto_resume_window_seconds` defaults to `7200`; `0` disables automatic restart continuation. Explicit Stop/Cancel, manual pauses, and unanswered approvals do not automatically continue. An operation whose external result is unknown requires reconciliation. Continuation preserves the original task identity, permissions, and resource admission, and requires a compatible runtime contract. This restores durable checkpoints, not arbitrary process memory or unsaved external application state.

Self-update continuation, replanning after a changed runtime contract, and original-conversation follow-up use this setting too, measured from the completed update’s last recorded activity. A continuously running update is not stopped by this continuation timer.

### Shared Terminal environments

Select a Terminal resource to attach its interactive view beside the conversation. The Agent and the user share the same shell, working directory, environment and output. Hiding the view or switching sessions does not terminate the shell. **Terminate terminal** explicitly requests process termination and requires confirmation; an acknowledged request is not evidence that the process has exited. **Share terminal** makes it discoverable to other sessions on that Desktop host; making it private revokes the current Agent binding.

Terminal input delivery does not indicate command success. After an uncertain input result, OpenProgram does not resend it. **Reconnect view** reads the current output again without replaying input. An unfinished human input fragment or changed input revision prevents conflicting Agent input until the Agent observes the terminal again.

### Common Agent resource interface

The `resource` tool provides `describe` plus provider-declared operations. Start with `resource(action="describe")`, then use the returned provider name and argument schema. The built-in `web` and `terminal` providers preserve their native identities and checks. For example, `resource(provider="terminal", action="list")` discovers available shells; `observe` returns the exact generation, binding, input revision and output cursor required for subsequent operations. Existing `web_use` and `terminal_use` remain available.

Providers only expose supported operations. Web `release` releases a control session and leaves the Page open; `close` closes the exact observed Page using its owned control binding. Terminal `release` releases Agent control and leaves its process running; `close` requests termination. Local Terminal operations require a live execution in the originating Desktop window and cannot bypass a task sandbox.

Trusted Python integrations can register a `ResourceProvider` with `openprogram.resource_interface.registry.register(...)`, declaring a JSON Schema for every supported operation and a callable adapter. The adapter is responsible for its native authorization and resource lifecycle. Registration rejects duplicate provider names and validates arguments before invoking the adapter. This is a trusted integration API, not a loader for arbitrary code supplied by the Agent or a webpage. Integrations can use the existing `openprogram.session_resources.resource_use(...)` context to report session usage in Resources. Enabled installed applications automatically register `application.<id>` from their operation manifest. Their session-associated instances open in Resources using the existing isolated application view.

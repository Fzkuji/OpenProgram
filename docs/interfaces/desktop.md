# Desktop App and built-in browser

The macOS Desktop App presents OpenProgram as a multi-pane workspace. Each pane can hold Files, a chat, the built-in Browser, or a Terminal, and panes can be split or moved between app windows without changing the underlying session or browser tab.

Install the architecture-matched DMG from [GitHub Releases](https://github.com/Fzkuji/OpenProgram/releases), copy `OpenProgram.app` to `/Applications`, and start that installed app. The current release is unsigned, so the first launch may require **System Settings → Privacy & Security → Open Anyway**. The complete installation steps are in [Installation](../install/install.md).

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

The Bookmarks manager has a folder tree, current-folder list, search, favicon display, and item menus. History is grouped by local date and uses compact rows with time, favicon, title, and domain. Desktop Browser data is separate from backend state: on macOS, History and the persistent `webtabs` partition live under `~/Library/Application Support/openprogram-desktop/`, while chats, projects, Programs, and worker configuration remain under `~/.openprogram/`. Clearing browser data does not delete that backend state.

## Importing an existing browser profile

On macOS, OpenProgram can discover local Google Chrome, Brave, Microsoft Edge, and Chromium profiles. Import is always explicit: choose the source browser, profile, and any of the supported data types.

| Data | Behavior |
|---|---|
| History | Copies up to the supported limit of HTTP/HTTPS visits and merges them into OpenProgram History |
| Bookmarks | Preserves the bookmarks-bar, other-bookmarks, mobile-bookmarks, and nested-folder structure while filtering invalid URLs and duplicates |
| Cookies | Uses a temporary source-browser process to decrypt eligible cookies, validates them, and writes them through Electron's cookie API; some sites still require a new login |

OpenProgram does not import passwords, payment or address autofill data, downloads, cache, localStorage, Service Workers, browser extensions, or extension storage. It does not modify the source profile.

## Agent access to a split Browser pane

When a chat turn has a visible built-in Browser pane in the same app window, OpenProgram attaches a bounded description of that exact WebTab to the turn before the first model response. The Agent receives the page title, origin, visible text, ARIA landmarks, and a browser-control tool. This works whether the Browser pane is on the left or right, in a picture-in-picture preview over chat, and does not require the app window or Browser pane to have operating-system focus.

If the Agent opens a page while you stay in chat, Desktop shows that live WebTab as a small corner preview. The preview can expand into a chat-and-page split, take over the center pane, or close without destroying the tab. Closing the preview only hides it; the page remains in the tab strip. The web UI (a browser tab, not the Desktop App) has no native BrowserView, so the same preview falls back to an iframe or an Open-in-new-tab control.

Actions remain bound to the originating window and WebTab. The default path uses DOM, ARIA, page text, and element references. A single current-viewport screenshot is used only for a visual task or when the page cannot be located structurally. The product does not add OCR, object detection, iterative crops, component memory, vision memory, or workflow replay to this path.

## Browser extensions

Chrome Web Store and Edge Add-ons pages open as ordinary webpages, but OpenProgram does not install browser extensions, download CRX packages, import extensions from another browser, or provide an extension manager. The app uses standard Electron/Chromium. Electron exposes only part of the Chrome Extensions API and explicitly does not target compatibility with arbitrary Chrome Web Store extensions; OpenProgram does not maintain a custom Chromium/Electron fork or add another browser runtime for extension compatibility. The Playwright Chromium shipped with the complete runtime belongs to the browser automation backend; it does not host the Desktop Browser Pane or extensions.

Use [OpenProgram Plugins](../capabilities/plugins.md), Skills, MCP servers, Programs, or agent tools to extend OpenProgram itself. These do not modify the embedded webpage runtime.

The maintained engineering specifications are [Built-in browser design](../reference/design/ui/built-in-browser.html) and the [Web Use / Computer Use boundary](../reference/design/integrations/web-use.html).

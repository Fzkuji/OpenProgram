Guide: target "app". Read and operate macOS application windows in the background: the user keeps the keyboard, mouse and front app, and nothing is raised or activated.

Each action below is written action(arguments). Send one as computer_use_agent(target="app", action="click", args={"app": "Calculator", "element_index": 3}), several as actions=[{"action": "click", "args": {"element_index": 3}}, …] with args={"app": …} naming the app once (up to 20 steps; they stop at the first error and the state comes back once, at the end; indices and coordinates in every step refer to the state before them).

Workflow
1. get_app_state(app) first (list_apps shows installed and running apps; a closed app is launched in the background). It returns a screenshot and the window's Accessibility tree, one element per line with an index. When an app exposes little to Accessibility (games, canvases, remote desktops), the state also lists the text read off the screenshot by OCR, each line with the pixel x,y of its centre.
2. Act by element_index whenever the tree has the element; element actions reach covered, hidden and minimized windows. Use x,y (pixels of the app's last screenshot) only for what the tree lacks: canvases, custom controls.
3. Every action returns its result and the tree lines that changed (indices you already have stay valid, also when the app redraws its window; new elements get new indices). Do not call get_app_state again just to check; call it when a step says an element no longer exists, or with full=true for the whole tree.
4. Put several deterministic steps (fill a form, type then press Return) in one actions list.
5. Other agents may operate the same app. A call (one action, a list, a read) keeps the app to itself until it returns, so another agent's call waits for it; when another agent acted on the app since you last looked, the result says so and the changes it lists include theirs.

Actions
- list_apps; list_windows(app): window ids, titles, minimized / hidden / other-Space state.
- A window on another Space takes clicks and element actions but not keys: there type_text and paste insert text into the focused field, and press_key fails.
- get_app_state(app, window? | window_id?, full?, include=all|tree|screenshot).
- zoom(app, x, y, width, height): a region of the last screenshot at full pixel density, for small text; coordinates stay those of the screenshot.
- find(app, text?, role?): elements of the last tree whose label, value or placeholder contains text; when none does, lines of the screenshot that show the text (OCR), with their x,y.
- click(app, element_index | x,y, mouse_button=left|right|middle, click_count=1..3, key=held modifiers like "cmd" or "shift+alt", duration=seconds to hold).
- type_text(app, text, element_index?): types at the focus (focusing element_index first). A newline in text presses Return, which sends or submits in many message boxes and forms; use paste for multi-line text. set_value replaces a field's whole value.
- press_key(app, key, element_index?, duration?): xdotool names: "Return", "Escape", "Tab", "super+c", "ctrl+shift+Tab", "KP_0", "F5". super/cmd is Command. Keys go to that app only, so system-wide shortcuts (Spotlight, Mission Control) cannot be pressed.
- set_value(app, element_index, value): text field, slider, checkbox.
- scroll(app, element_index | x,y, direction=up|down|left|right, pages=1 | pixels).
- drag(app, x, y, to_x, to_y).
- hover(app, element_index | x,y, duration=0.8): rest the pointer there (the real cursor does not move) to open tooltips and hover menus; apps that ignore the pointer while in the background show nothing.
- wait(app, duration=1): let up to 10 seconds pass, then get what changed; for loading, playback or a reply.
- perform_secondary_action(app, element_index, action_name): one of the element's listed actions (AXShowMenu, AXIncrement …).
- select_text(app, element_index, text, prefix?, suffix?, selection_type=text|cursor_before|cursor_after).
- paste(app, text, format=text|md|html, element_index?): through the clipboard, which is restored afterwards; md for formatted text in rich editors.
- copy(app, element_index?): the app's own Copy on the current selection; returns the copied text and restores the clipboard.
- focus(app, element_index): move keyboard focus without clicking.
- menu(app, menu_path?): no path lists the menu bar; a path such as ["File", "Export"] lists that menu (shortcuts, disabled items) or chooses a leaf item. Titles match case-insensitively, a trailing "…" optional.
- show_window(app, window? | window_id?): un-minimize a window or unhide the app, still in the background.
- open_file(app, path): open a document (absolute path) in that app the way Open With does, without raising it; the next state is of its window. Use it instead of File > Open or cmd+O: the system Open and Save dialogs take clicks and set_value but not keys. When a state says a file dialog is open, call open_file: it cancels the dialog first.
- start_audio_recording(app, max_duration=60): record the sound that app plays (only that app; the user still hears it), up to 300 seconds; act meanwhile. stop_audio_recording(app, transcribe=true) stops, saves a WAV and returns its path and, when a speech engine is set up in Settings > Voice, a transcript; a model that accepts audio input also receives the recording itself.

OpenProgram's own windows are refused outside Bypass permissions mode. The owner can refuse more applications (`computer_use.refused_apps`) and typing into password fields (`computer_use.refuse_password_fields`).

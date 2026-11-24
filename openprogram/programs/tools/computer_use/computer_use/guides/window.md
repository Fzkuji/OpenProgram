Guide: type="window". Read and operate one application's windows by their element tree and their screenshot; app names the application and title picks one of its windows. The target note says what differs on its operating system and how its keys are named.

Each action below is written action(arguments). Send one as computer_use(type="window", app="Calculator", action="click", args={"element": 3}), several as actions=[{"action": "click", "args": {"element": 3}}, …] with app naming the application once (up to 20 steps; they stop at the first error and the state comes back once, at the end; elements and coordinates in every step refer to the state before them).

Workflow
1. get_state with app first (list_apps shows the installed and running applications; a closed one is started). app matches without regard to case ("safari" is Safari), and a browser's short name ("chrome", "edge", "brave") names its application; title (part of a window's title, or its window_id) picks one of its windows. It returns a screenshot and the window's element tree, one element per line with its number. When an application exposes little in its tree (games, canvases, remote desktops), the state also lists the text read off the screenshot, each line with the pixel x,y of its centre.
2. Point by element whenever the tree has it; element steps reach covered, hidden and minimized windows. Use x,y (pixels of the last screenshot) only for what the tree lacks: canvases, custom controls.
3. Every step returns its result and the tree lines that changed (the numbers you have stay valid, also when the application redraws its window; new elements get new numbers). Do not call get_state again just to check; call it when a step answers ELEMENT_GONE, or with full=true for the whole tree.
4. Put several steps that need no look in between (fill a form, type then submit) in one actions list.
5. Other agents may operate the same application. A call keeps it to itself until it returns, so another agent's call waits; when another agent acted on it since you last looked, the result says so and the changes it lists include theirs.

Actions
- list_apps: the installed and running applications.
- list_windows: the windows of app, with their window_id, title and state.
- get_state(include=all|tree|screenshot, full=false): the window's screenshot and element tree; title (part of a window's title, or its window_id) picks the window. Without full, the tree shows what changed since you last read it.
- zoom(x, y, width, height): a region of the last screenshot at full pixel density, for small text; coordinates stay those of the screenshot.
- find(text?, role?): elements of the last tree whose label, value or placeholder contains text, or that have role; when none does, the lines of the screenshot that show the text, with their x,y.
- click(element | x,y, mouse_button=left|right|middle, click_count=1..3, modifiers, seconds): modifiers are keys held during the click ("shift", "super+alt"); seconds holds the button down.
- type_text(text, element?, clear=false, submit=false): inserts text at the focus (focusing element first); clear empties the field first, submit presses Return after. A newline in text presses Return, which sends or submits in many message boxes and forms; use paste for text of several lines. set_value replaces a field's whole value.
- press_key(key, element?, seconds?): a key or chord in xdotool names: "Return", "Escape", "Tab", "ctrl+shift+Tab", "KP_0", "F5"; seconds holds it down. The target note says what super is there.
- set_value(element, value): the whole value of a text field, slider or checkbox.
- scroll(element | x,y, direction=up|down|left|right, pages=1 | pixels).
- drag(element | x,y, to_element | to_x,to_y, seconds?): press, move and release; seconds slows the move.
- hover(element | x,y, seconds=0.8): rest the pointer there to open tooltips and hover menus.
- wait(seconds=1, text?): let up to 10 seconds pass, then get what changed, for loading, playback or a reply; with text, wait until the window shows it (up to 10 seconds when seconds is not given).
- focus(element): move keyboard focus without clicking.
- show_window: bring back a minimized window (title) or a hidden application.
- menu(menu_path?): no path lists the menu bar; a path such as ["File", "Export"] lists that menu (shortcuts, disabled items) or chooses a leaf item. Titles match without regard to case, a trailing "…" optional.
- paste(text, format=text|md|html, element?): put text in at the focus at once; md for formatted text in rich editors.
- copy(element?): the selected text of the element, or of the focused one.
- select_text(element, text, prefix?, suffix?, selection_type=text|cursor_before|cursor_after): select text inside a field, or put the cursor before or after it.
- perform_secondary_action(element, action_name): one of the actions the element's line lists.
- open_file(path): open a document (an absolute path on the target's machine) in app, where the target has it.
- start_audio_recording(max_duration=60): record the sound app plays (only that application; the user still hears it), up to 300 seconds; act meanwhile. stop_audio_recording(transcribe=true) stops, saves a WAV and returns its path and, when a speech engine is set up in Settings > Voice, a transcript; a model that accepts audio input also receives the recording itself.

OpenProgram's own windows are refused outside Bypass permissions mode. The owner can refuse more applications (`computer_use.refused_apps`) and typing into password fields (`computer_use.refuse_password_fields`).

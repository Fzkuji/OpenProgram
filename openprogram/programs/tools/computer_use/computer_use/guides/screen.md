Guide: type="screen". Operate a whole display by its pixels, as a person at it would: the pointer moves and keys go to the front application. The target's note says what that target has and lacks.

Send one action as computer_use(type="screen", action="click", args={"x": 640, "y": 412}), several as actions=[{"action": "click", "args": {"x": 640, "y": 412}}, {"action": "type_text", "args": {"text": "Ada"}}] (up to 20, in order, stopping at the first error). display names the display once for every step.

How it works
- There is no element tree. get_state returns a screenshot, and x,y in every step are pixels of the last screenshot of that display. Each call that acts returns a new screenshot; aim at what it shows, not at what an older one showed.
- Click a window before typing into it: keys and text go to whichever application is in front.
- A step is never sent twice. When an error says a step may have taken effect, read the state before doing it again.

Actions
- get_state: a screenshot of the display; the main one without display.
- list_displays: the displays with their ids and sizes, and which is the main one.
- click(x, y, mouse_button=left|right|middle, click_count=1..3, modifiers="super" | "shift+alt", seconds=hold).
- type_text(text, clear=false, submit=false): types at the focus; clear empties the field first, submit presses Return after.
- press_key(key, seconds?): xdotool names, such as "Return", "Escape", "Tab", "ctrl+shift+Tab"; super is Command on macOS and the Windows key elsewhere.
- scroll(x?, y?, direction=down|up|left|right, pages=1 | pixels): the wheel at that point, or where the pointer is.
- drag(x, y, to_x, to_y, seconds?): press at one point, move to the other, release.
- hover(x, y, seconds=0.8): rest the pointer there, for tooltips and hover menus.
- wait(seconds=1): let up to 10 seconds pass, then look again.
- zoom(x, y, width, height): a region of the last screenshot at full resolution, for small text; x,y in later steps still refer to the screenshot.
- paste(text): through the clipboard, for long or multi-line text; a newline in type_text presses Return.

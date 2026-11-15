Guide: target "vm". Operate the remote virtual machine at the address set in Settings (computer_use.vm_url). It shows only pixels: there is no element tree, so every pointer action takes x,y in pixels of the last screenshot.

Each action below is written action(arguments). Send one as computer_use_agent(target="vm", action="click", args={"x": 640, "y": 360}), several as actions=[…] (up to 20; they stop at the first error). Every acting call returns a new screenshot.

Actions
- screenshot: the whole screen.
- click(x,y, mouse_button=left|right|middle, click_count=1..3).
- move(x,y): put the pointer there, for hover menus.
- drag(x, y, to_x, to_y).
- scroll(x?, y?, direction=up|down|left|right, clicks=5).
- type_text(text): types at the focus.
- press_key(key): one key or a chord in pyautogui names: "enter", "esc", "tab", "ctrl+c", "ctrl+shift+t".
- wait(duration=1): up to 10 seconds, then a screenshot.

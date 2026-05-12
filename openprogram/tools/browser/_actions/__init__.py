"""Browser-tool action handlers split out of browser.py.

Each module owns one topic of verbs (open/lifecycle/interact/read/etc.)
and reaches the shared session table + helpers via
``from openprogram.tools.browser import browser as _b`` — that's a
lazy attribute reach, fine because action modules are only imported
inside ``browser.execute()`` (which fires long after browser.py has
finished loading).

Modules:
    open_action.py  — _start_engine / _read_cdp_port / _open
    interact.py     — navigate / click / type / hover / select / press / upload / wait / eval
    read.py         — extract / html / accessibility / screenshot / screenshot_b64 / cookies
    console.py      — console / console_subscribe / block / frames / frame_eval
    tabs.py         — tabs / new_tab / switch_tab / download / viewport
    lifecycle.py    — save_login / close / list
"""

import os
import re
import time

import pytest


pytestmark = pytest.mark.live


def _terminal_text(page, label: str) -> str:
    host = page.locator(f'[aria-label="{label}"]').filter(has=page.locator(".xterm-rows"))
    return host.locator(".xterm-rows").inner_text()


def _wait_for_pattern(page, label: str, pattern: str, timeout: int = 10_000) -> str:
    page.wait_for_function(
        """([label, pattern]) => {
          const host = [...document.querySelectorAll('[aria-label]')]
            .find((node) => node.getAttribute('aria-label') === label && node.querySelector('.xterm-rows'));
          return new RegExp(pattern, 'm').test(host?.querySelector('.xterm-rows')?.innerText ?? '');
        }""",
        arg=[label, pattern],
        timeout=timeout,
    )
    return _terminal_text(page, label)


def _type_command(page, label: str, command: str) -> None:
    textarea = page.locator(f'[aria-label="{label}"] .xterm-helper-textarea')
    textarea.focus()
    page.keyboard.type(command)
    page.keyboard.press("Enter")


def _open_new_tab(page) -> None:
    page.get_by_role("button", name=re.compile(r"^(New tab|新标签页)$")).click()


def _close_builtin_tabs(page) -> None:
    for tab_id in ("b:claude", "b:terminal"):
        tab = page.locator(f'[role="tab"][data-tab-id="{tab_id}"]')
        if tab.count():
            tab.locator("xpath=..").get_by_label(re.compile(r"^(Close tab|关闭标签)$")).click()
    page.wait_for_timeout(300)


def _wait_for_process_exit(pid: int, timeout: float = 4.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    pytest.fail(f"terminal process {pid} remained alive after its tab closed")


def test_packaged_app_opens_real_terminal_and_claude_code():
    if os.environ.get("OPENPROGRAM_TEST_LIVE") != "1":
        pytest.skip("set OPENPROGRAM_TEST_LIVE=1 to inspect the installed App")

    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.connect_over_cdp("http://127.0.0.1:9223")
        page = None
        live_pids: set[int] = set()
        try:
            pages = [
                candidate
                for context in browser.contexts
                for candidate in context.pages
                if candidate.url.startswith("http://localhost:18100")
                or candidate.url.startswith("http://127.0.0.1:18100")
            ]
            assert pages, "the default /Applications/OpenProgram.app page is not available on CDP 9223"
            page = pages[0]
            page.bring_to_front()
            _close_builtin_tabs(page)

            _open_new_tab(page)
            page.get_by_role("button", name=re.compile(r"^(Terminal|终端)$")).click()
            terminal = page.locator('[aria-label="Terminal"], [aria-label="终端"]').filter(
                has=page.locator(".xterm")
            )
            terminal.locator(".xterm-helper-textarea").wait_for(state="attached")
            label = terminal.get_attribute("aria-label")

            marker = f"OP_TERM_{time.time_ns()}"
            _type_command(page, label, f"echo {marker}:$$")
            text = _wait_for_pattern(page, label, rf"^{marker}:\d+$")
            pid = re.search(rf"{marker}:(\d+)", text)
            assert pid, text
            terminal_pid = int(terminal.get_attribute("data-process-id") or "0")
            assert terminal_pid == int(pid.group(1))
            live_pids.add(terminal_pid)

            _type_command(page, label, "sleep 30")
            page.wait_for_timeout(250)
            terminal.locator(".xterm-helper-textarea").focus()
            page.keyboard.press("Control+C")
            interrupt_marker = f"OP_INTERRUPT_{time.time_ns()}"
            _type_command(page, label, f"echo {interrupt_marker}")
            _wait_for_pattern(page, label, rf"^{interrupt_marker}$")

            _type_command(page, label, "stty size; echo OP_SIZE_WIDE")
            wide = _wait_for_pattern(page, label, r"^OP_SIZE_WIDE$")
            wide_sizes = re.findall(r"(?:^|\n)\s*(\d+)\s+(\d+)\s*(?:\n|$)", wide)
            assert wide_sizes, wide
            wide_cols = int(wide_sizes[-1][1])
            terminal.evaluate("node => { node.dataset.oldWidth = node.style.width; node.style.width = '420px'; }")
            page.wait_for_timeout(500)
            _type_command(page, label, "stty size; echo OP_SIZE_NARROW")
            narrow = _wait_for_pattern(page, label, r"^OP_SIZE_NARROW$")
            narrow_sizes = re.findall(r"(?:^|\n)\s*(\d+)\s+(\d+)\s*(?:\n|$)", narrow)
            assert narrow_sizes, narrow
            assert int(narrow_sizes[-1][1]) < wide_cols
            terminal.evaluate("node => { node.style.width = node.dataset.oldWidth || ''; }")

            _open_new_tab(page)
            page.get_by_role("button", name="Claude Code", exact=True).click()
            claude = page.get_by_label("Claude Code terminal", exact=True)
            claude.locator(".xterm-helper-textarea").wait_for(state="attached")
            page.wait_for_function(
                """() => (document.querySelector('[aria-label="Claude Code terminal"] .xterm-rows')
                  ?.textContent?.trim().length ?? 0) > 10""",
                timeout=20_000,
            )
            claude_text = _terminal_text(page, "Claude Code terminal")
            assert re.search(r"Claude Code\s+v?\d", claude_text), claude_text
            assert "command not found" not in claude_text
            assert "[process exited" not in claude_text
            claude_pid = int(claude.get_attribute("data-process-id") or "0")
            assert claude_pid > 0
            live_pids.add(claude_pid)

            page.locator('[role="tab"][data-tab-id="b:terminal"]').click()
            resumed_marker = f"OP_RESUMED_{time.time_ns()}"
            _type_command(page, label, f"echo {resumed_marker}:$$")
            resumed = _wait_for_pattern(page, label, rf"^{resumed_marker}:\d+$")
            resumed_pid = re.search(rf"{resumed_marker}:(\d+)", resumed)
            assert resumed_pid and resumed_pid.group(1) == pid.group(1)

            claude_tab = page.locator('[role="tab"][data-tab-id="b:claude"]')
            claude_tab.locator("xpath=..").get_by_label(re.compile(r"^(Close tab|关闭标签)$")).click()
            _wait_for_process_exit(claude_pid)
            live_pids.remove(claude_pid)
            final_marker = f"OP_AFTER_CLOSE_{time.time_ns()}"
            _type_command(page, label, f"echo {final_marker}")
            _wait_for_pattern(page, label, rf"^{final_marker}$")
        finally:
            if page is not None:
                _close_builtin_tabs(page)
            for pid in live_pids:
                _wait_for_process_exit(pid)

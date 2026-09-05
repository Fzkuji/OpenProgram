import os
import re
import time

import pytest


pytestmark = pytest.mark.live


def _pid_marker_command(marker: str) -> str:
    if os.name == "nt":
        return f'Write-Output "{marker}:$PID"'
    return f"echo {marker}:$$"


def _marker_command(marker: str) -> str:
    if os.name == "nt":
        return f'Write-Output "{marker}"'
    return f"echo {marker}"


def _sleep_command(seconds: int) -> str:
    if os.name == "nt":
        return f"Start-Sleep -Seconds {seconds}"
    return f"sleep {seconds}"


def _size_command(marker: str) -> str:
    if os.name == "nt":
        return (
            '$size = $Host.UI.RawUI.WindowSize; '
            'Write-Output "$($size.Height) $($size.Width)"; '
            f'Write-Output "{marker}"'
        )
    return f"stty size; echo {marker}"


def _terminal_text(page, label: str) -> str:
    host = page.locator(f'[aria-label="{label}"]').filter(has=page.locator(".xterm-rows"))
    return host.locator(".xterm-rows").inner_text()


def _wait_for_pattern(page, label: str, pattern: str, timeout: int = 10_000) -> str:
    try:
        page.wait_for_function(
            """([label, pattern]) => {
              const host = [...document.querySelectorAll('[aria-label]')]
                .find((node) => node.getAttribute('aria-label') === label && node.querySelector('.xterm-rows'));
              return new RegExp(pattern, 'm').test(host?.querySelector('.xterm-rows')?.innerText ?? '');
            }""",
            arg=[label, pattern],
            timeout=timeout,
        )
    except Exception as exc:
        pytest.fail(
            f"terminal pattern {pattern!r} did not appear: {exc}\n"
            f"terminal output:\n{_terminal_text(page, label)}"
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


def _process_is_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and (
                exit_code.value == still_active
            )
        finally:
            kernel32.CloseHandle(handle)

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for_process_exit(pid: int, timeout: float = 4.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _process_is_alive(pid):
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
            assert pages, "the installed OpenProgram page is not available on CDP 9223"
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
            pane = terminal.locator("xpath=..")
            pane.get_by_role("status").filter(has_text=re.compile(r"Running|运行中")).wait_for()
            titles = pane.locator("[title]").evaluate_all(
                "nodes => nodes.map(node => node.getAttribute('title') || '')"
            )
            assert any(
                title.startswith("/")
                or re.match(r"^[A-Za-z]:[\\/]", title)
                or "主目录" in title
                or "Home directory" in title
                for title in titles
            ), titles

            marker = f"OP_TERM_{time.time_ns()}"
            _type_command(page, label, _pid_marker_command(marker))
            text = _wait_for_pattern(page, label, rf"^{marker}:\d+$")
            pid = re.search(rf"{marker}:(\d+)", text)
            assert pid, text
            terminal_pid = int(terminal.get_attribute("data-process-id") or "0")
            assert terminal_pid == int(pid.group(1))
            live_pids.add(terminal_pid)

            clear_marker = f"OP_CLEAR_{time.time_ns()}"
            _type_command(page, label, _marker_command(clear_marker))
            _wait_for_pattern(page, label, rf"^{clear_marker}$")
            pane.get_by_role("button", name=re.compile(r"^(Clear terminal|清屏)$")).click()
            page.wait_for_timeout(100)
            assert clear_marker not in _terminal_text(page, label)

            paste_marker = f"OP_PASTE_{time.time_ns()}"
            page.evaluate(
                "command => navigator.clipboard.writeText(command)",
                _marker_command(paste_marker),
            )
            pane.get_by_role("button", name=re.compile(r"^(Paste|粘贴)$")).click()
            page.keyboard.press("Enter")
            _wait_for_pattern(page, label, rf"^{paste_marker}$")

            pane.get_by_role("button", name=re.compile(r"^(Stop process|停止进程)$")).click()
            pane.get_by_role("status").filter(has_text=re.compile(r"Stopped|已停止")).wait_for()
            _wait_for_process_exit(terminal_pid)
            live_pids.remove(terminal_pid)
            pane.get_by_role("button", name=re.compile(r"^(Restart process|重启进程)$")).click()
            pane.get_by_role("status").filter(has_text=re.compile(r"Running|运行中")).wait_for()
            page.wait_for_function(
                """([label, oldPid]) => {
                  const host = [...document.querySelectorAll('[aria-label]')]
                    .find((node) => node.getAttribute('aria-label') === label && node.querySelector('.xterm'));
                  return Number(host?.dataset.processId ?? 0) > 0
                    && Number(host?.dataset.processId) !== oldPid;
                }""",
                arg=[label, terminal_pid],
            )
            terminal_pid = int(terminal.get_attribute("data-process-id") or "0")
            live_pids.add(terminal_pid)
            restarted_marker = f"OP_RESTARTED_{time.time_ns()}"
            _type_command(page, label, _pid_marker_command(restarted_marker))
            restarted = _wait_for_pattern(page, label, rf"^{restarted_marker}:\d+$")
            restarted_pid = re.search(rf"{restarted_marker}:(\d+)", restarted)
            assert restarted_pid and int(restarted_pid.group(1)) == terminal_pid

            _type_command(page, label, _sleep_command(30))
            page.wait_for_timeout(250)
            terminal.locator(".xterm-helper-textarea").focus()
            page.keyboard.press("Control+C")
            # ConPTY delivers ETX immediately, but PowerShell briefly flushes
            # input while it unwinds the interrupted pipeline. Typing in the
            # same automation tick can lose the first few characters even
            # though the interrupt succeeded and the prompt returned.
            page.wait_for_timeout(250)
            interrupt_marker = f"OP_INTERRUPT_{time.time_ns()}"
            _type_command(page, label, _marker_command(interrupt_marker))
            _wait_for_pattern(page, label, rf"^{interrupt_marker}$")

            _type_command(page, label, _size_command("OP_SIZE_WIDE"))
            wide = _wait_for_pattern(page, label, r"^OP_SIZE_WIDE$")
            wide_sizes = re.findall(r"(?:^|\n)\s*(\d+)\s+(\d+)\s*(?:\n|$)", wide)
            assert wide_sizes, wide
            wide_cols = int(wide_sizes[-1][1])
            terminal.evaluate("node => { node.dataset.oldWidth = node.style.width; node.style.width = '420px'; }")
            page.wait_for_timeout(500)
            _type_command(page, label, _size_command("OP_SIZE_NARROW"))
            narrow = _wait_for_pattern(page, label, r"^OP_SIZE_NARROW$")
            narrow_sizes = re.findall(r"(?:^|\n)\s*(\d+)\s+(\d+)\s*(?:\n|$)", narrow)
            assert narrow_sizes, narrow
            assert int(narrow_sizes[-1][1]) < wide_cols
            terminal.evaluate("node => { node.style.width = node.dataset.oldWidth || ''; }")

            _type_command(page, label, "claude --version")
            claude_version = _wait_for_pattern(
                page,
                label,
                r"^\d+\.\d+\.\d+ \(Claude Code\)$",
                timeout=20_000,
            )
            assert "command not found" not in claude_version

            _open_new_tab(page)
            page.locator('[role="tab"][data-tab-id="b:terminal"]').click()
            resumed_marker = f"OP_RESUMED_{time.time_ns()}"
            _type_command(page, label, _pid_marker_command(resumed_marker))
            resumed = _wait_for_pattern(page, label, rf"^{resumed_marker}:\d+$")
            resumed_pid = re.search(rf"{resumed_marker}:(\d+)", resumed)
            assert resumed_pid and int(resumed_pid.group(1)) == terminal_pid

            final_marker = f"OP_AFTER_CLOSE_{time.time_ns()}"
            _type_command(page, label, _marker_command(final_marker))
            _wait_for_pattern(page, label, rf"^{final_marker}$")
        finally:
            if page is not None:
                _close_builtin_tabs(page)
            for pid in live_pids:
                _wait_for_process_exit(pid)

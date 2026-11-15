"""Copying text uses the tool each platform ships; no Python clipboard package is needed."""
from types import SimpleNamespace

import pytest

from openprogram.host._platform import clipboard


def _has(*names):
    return lambda name: f"/tools/{name}" if name in names else None


@pytest.mark.parametrize("platform, tools, environ, command, encoding", [
    ("darwin", ("pbcopy",), {}, ["/tools/pbcopy"], "utf-8"),
    # Not run on Windows: clip.exe is a stand-in. It needs UTF-16 with a mark to keep non-ASCII text.
    ("win32", ("clip.exe",), {}, ["/tools/clip.exe"], "utf-16"),
    ("linux", ("wl-copy", "xclip"), {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}, ["/tools/wl-copy"], "utf-8"),
    ("linux", ("xclip", "xsel"), {"DISPLAY": ":0"}, ["/tools/xclip", "-selection", "clipboard"], "utf-8"),
    ("linux", ("xsel",), {"DISPLAY": ":0"}, ["/tools/xsel", "--clipboard", "--input"], "utf-8"),
])
def test_each_platform_has_its_copy_command(platform, tools, environ, command, encoding):
    assert clipboard.copy_command(platform, _has(*tools), environ) == (command, encoding)
    assert clipboard.support(platform, _has(*tools), environ) == {"available": True, "reason": ""}


@pytest.mark.parametrize("platform, tools, environ, names", [
    ("linux", ("xclip", "wl-copy"), {}, ("Wayland", "X11")),          # no graphical session
    ("linux", (), {"DISPLAY": ":0"}, ("wl-copy", "xclip", "xsel")),    # no tool
    ("linux", ("wl-copy",), {"DISPLAY": ":0"}, ("xclip",)),            # the tool of the other session type
    ("win32", (), {}, ("clip.exe",)),
    ("darwin", (), {}, ("pbcopy",)),
])
def test_a_host_without_a_clipboard_says_what_is_missing(platform, tools, environ, names):
    assert clipboard.copy_command(platform, _has(*tools), environ) is None
    state = clipboard.support(platform, _has(*tools), environ)
    assert state["available"] is False and all(name in state["reason"] for name in names)


def test_copy_sends_the_text_in_the_tools_encoding_and_leaves_it_no_pipe(monkeypatch):
    ran = []
    monkeypatch.setattr(clipboard, "copy_command", lambda: (["clip.exe"], "utf-16"))
    monkeypatch.setattr(clipboard.subprocess, "run",
                        lambda command, **options: ran.append((command, options)) or SimpleNamespace(returncode=0))
    assert clipboard.copy("答案 ok") is True
    command, options = ran[0]
    assert command == ["clip.exe"] and options["input"].decode("utf-16") == "答案 ok"
    assert options["input"][:2] in (b"\xff\xfe", b"\xfe\xff")
    assert options["stdout"] == options["stderr"] == clipboard.subprocess.DEVNULL


@pytest.mark.parametrize("failure", [OSError("gone"), clipboard.subprocess.TimeoutExpired("xclip", 5)])
def test_copy_is_false_when_the_tool_fails(monkeypatch, failure):
    def run(*_args, **_options):
        raise failure
    monkeypatch.setattr(clipboard, "copy_command", lambda: (["xclip"], "utf-8"))
    monkeypatch.setattr(clipboard.subprocess, "run", run)
    assert clipboard.copy("text") is False
    monkeypatch.setattr(clipboard.subprocess, "run", lambda *_a, **_k: SimpleNamespace(returncode=1))
    assert clipboard.copy("text") is False
    monkeypatch.setattr(clipboard, "copy_command", lambda: None)
    assert clipboard.copy("text") is False


def test_the_repl_copies_the_last_reply_without_an_extra_package(monkeypatch):
    """`/copy` used pyperclip, which the product does not install: it never copied anywhere."""
    from openprogram.interfaces.cli.repl import handlers
    from openprogram.interfaces.server import persistence

    copied, printed = [], []
    monkeypatch.setattr(persistence, "load_session", lambda _agent, _session: {
        "messages": [{"role": "user", "content": "q"}, {"role": "assistant", "content": "the answer"}]})
    monkeypatch.setattr(clipboard, "copy", lambda text: copied.append(text) or True)
    monkeypatch.setitem(__import__("sys").modules, "pyperclip", None)
    console = SimpleNamespace(print=lambda text: printed.append(text))
    handlers._handle_copy(console, SimpleNamespace(id="main"), "s1")
    assert copied == ["the answer"] and "Copied 10 chars" in printed[0]

    monkeypatch.setattr(clipboard, "copy", lambda _text: False)
    monkeypatch.setattr(clipboard, "support", lambda: {"available": False, "reason": "clip.exe is not on PATH."})
    printed.clear()
    handlers._handle_copy(console, SimpleNamespace(id="main"), "s1")
    assert "clip.exe is not on PATH." in printed[0] and printed[1] == "the answer"

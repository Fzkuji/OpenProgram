"""Showing a file in the file manager, per platform."""
import pytest

from openprogram.host._platform import file_manager


def test_macos_selects_the_file_in_finder():
    assert file_manager.reveal_command("/Users/a b/report.md", "darwin") == ["open", "-R", "/Users/a b/report.md"]


@pytest.mark.parametrize("target, line", [
    # Not run on Windows. A path with a space must stay one quoted path after /select, and Explorer
    # does not take forward slashes.
    (r"C:\Users\Ada Lovelace\notes\plan.md", r'explorer.exe /select,"C:\Users\Ada Lovelace\notes\plan.md"'),
    ("C:/Users/Ada Lovelace/notes/plan.md", r'explorer.exe /select,"C:\Users\Ada Lovelace\notes\plan.md"'),
    (r"\\server\share\a, b.txt", r'explorer.exe /select,"\\server\share\a, b.txt"'),
])
def test_windows_gives_explorer_one_command_line(target, line):
    command = file_manager.reveal_command(target, "win32")
    # A list would be re-quoted by subprocess as "/select,C:\Users\Ada Lovelace\...", one quoted argument.
    assert isinstance(command, str) and command == line


def test_linux_opens_the_containing_folder(tmp_path):
    file = tmp_path / "plan.md"
    file.write_text("x")
    found = {"which": lambda name: f"/usr/bin/{name}", "environ": {"DISPLAY": ":0"}}
    assert file_manager.reveal_command(str(file), "linux", **found) == ["/usr/bin/xdg-open", str(tmp_path)]
    assert file_manager.reveal_command(str(tmp_path), "linux", **found) == ["/usr/bin/xdg-open", str(tmp_path)]
    wayland = {"which": lambda name: f"/usr/bin/{name}", "environ": {"WAYLAND_DISPLAY": "wayland-0"}}
    assert file_manager.reveal_command(str(file), "linux", **wayland)[0] == "/usr/bin/xdg-open"


def test_linux_without_a_desktop_says_so():
    with pytest.raises(file_manager.NoFileManager, match="no graphical session"):
        file_manager.reveal_command("/srv/x", "linux", which=lambda name: name, environ={})
    with pytest.raises(file_manager.NoFileManager, match="needs xdg-open"):
        file_manager.reveal_command("/srv/x", "linux", which=lambda _name: None, environ={"DISPLAY": ":0"})


def test_the_reveal_action_answers_with_the_reason_instead_of_an_errno(monkeypatch, tmp_path):
    from openprogram.interfaces.server.ws_actions.files import mutations

    started = []
    monkeypatch.setattr(mutations.subprocess, "Popen", lambda command: started.append(command))
    monkeypatch.setattr(file_manager.sys, "platform", "linux")
    monkeypatch.setattr(file_manager.shutil, "which", lambda _name: None)
    monkeypatch.setenv("DISPLAY", ":0")
    assert mutations.reveal_in_file_manager(str(tmp_path)) == {
        "error": "Showing a file on Linux needs xdg-open (the xdg-utils package)."}
    assert started == []
    monkeypatch.setattr(file_manager.sys, "platform", "win32")
    assert mutations.reveal_in_file_manager(r"C:\Users\Ada Lovelace\plan.md") == {"ok": True}
    assert started == [r'explorer.exe /select,"C:\Users\Ada Lovelace\plan.md"']

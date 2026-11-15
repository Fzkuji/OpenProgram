"""Product code reads and writes files the same way on every system.

Python picks the text encoding from the system locale when a call names
none: UTF-8 on macOS and Linux, the ANSI code page on Windows (GBK on a
Chinese system, cp1252 on a Western one). A file written that way on Windows
cannot be read back as UTF-8, and a character outside the code page fails the
write. So every text open in ``openprogram/`` and ``scripts/`` names its
encoding, and so does every text-mode subprocess under ``openprogram/state``.

Two more rules cover what Windows does differently below the encoding:

* A file in ``openprogram/`` is published with
  ``host/_platform/filesystem.replace_file``, which waits out the moment
  another handle holds the destination. A bare ``os.replace`` or
  ``Path.replace`` fails there. ``KNOWN_BARE_REPLACE`` names the renames
  that are not such a publish, each with its reason.
* A descriptor from ``os.open`` that ``os.write`` writes to carries
  ``O_BINARY``; without it Windows turns each LF into CRLF and byte offsets
  stop matching.

The scan reads source, so it sees calls inside functions too. ``KNOWN``
lists what is left, by file and count; a number there only goes down.
"""
from __future__ import annotations

import ast
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PRODUCT = ("openprogram", "scripts")
_MODE = re.compile(r"[rwaxtb+]+")
_TEMPORARY = {"NamedTemporaryFile", "TemporaryFile", "SpooledTemporaryFile"}
_SUBPROCESS = {"run", "Popen", "check_output", "check_call", "call"}
# Modules whose ``open`` is not a text open of a path.
_OTHER_OPENS = {"os", "io", "webbrowser", "tarfile", "zipfile", "gzip", "bz2", "lzma"}
# ``x.replace(y)`` on these is not a file move.
_NOT_A_PATH = {"dataclasses", "copy"}
_WRITE_FLAGS = ("O_WRONLY", "O_RDWR", "O_APPEND")

KNOWN_TEXT_OPENS = {
    # Lock files opened in append mode: nothing is read from or written to them.
    "openprogram/state/store/snapshot/checkpoint/prompt_checkpoints.py": 1,
    "openprogram/state/store/snapshot/checkpoint/store.py": 4,
    # Reads a JSON credential file.
    "openprogram/models/providers/adapters/xai_subscription/auth_adapter.py": 1,
}
KNOWN_SUBPROCESS_TEXT = {
    "openprogram/state/store/project/project_store.py": 1,
}
KNOWN_BARE_REPLACE = {
    # Each entry is a rename that is not a publish through the seam, and why.
    # The seam itself.
    "openprogram/host/_platform/filesystem.py": 1,
    # Inside the seam's retry, with extended-length paths on both sides.
    "openprogram/state/store/session/atomic_file.py": 1,
    # Inside its own 30 second wait for a process exiting from the directory.
    "openprogram/operations/self_update/versions/build.py": 1,
    # The POSIX branch; Windows moves the pointer with MoveFileExW and retries.
    "openprogram/operations/self_update/versions/pointer.py": 1,
    # A discard into trash: a tree a running version holds waits for the next pass.
    "openprogram/operations/self_update/versions/retention.py": 1,
    # Losing to a process that extracted the same commit is the handled outcome.
    "openprogram/programs/_host/packages.py": 1,
    # Log rotation under the log lock: a held log is appended to and rotated later.
    "openprogram/runtime/events/event_log.py": 1,
}
KNOWN_TEXT_DESCRIPTORS = {
    # Lock files (2) and the recording's header and rows (3).
    "openprogram/models/providers/recording/recording.py": 5,
    # macOS only: the flags name O_NOFOLLOW without a fallback.
    "openprogram/host/system_access/identity.py": 1,
}


def _sources(*roots: str):
    for root in roots:
        for path in sorted((ROOT / root).rglob("*.py")):
            relative = path.relative_to(ROOT).as_posix()
            if "/tests/" in relative or "__pycache__" in relative:
                continue
            source = path.read_text(encoding="utf-8")
            yield relative, source, ast.parse(source)


def _calls(tree: ast.AST):
    return (node for node in ast.walk(tree) if isinstance(node, ast.Call))


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _splat(call: ast.Call) -> bool:
    return any(keyword.arg is None for keyword in call.keywords)


def _mode(call: ast.Call, position: int, default: str | None) -> str | None:
    """The literal mode a call opens with, or ``None`` when it is not one."""
    value = _keyword(call, "mode")
    if value is None and len(call.args) > position:
        value = call.args[position]
    if value is None:
        return default
    if isinstance(value, ast.Constant) and isinstance(value.value, str) and _MODE.fullmatch(value.value):
        return value.value
    return None


def _opens_text_without_encoding(call: ast.Call) -> bool:
    if _keyword(call, "encoding") is not None or _splat(call):
        return False
    function = call.func
    if isinstance(function, ast.Name):
        if function.id != "open":
            return False
        mode = _mode(call, 1, "r")
        return mode is not None and "b" not in mode and len(call.args) < 4
    if not isinstance(function, ast.Attribute):
        return False
    name = function.attr
    if name == "read_text":
        return not call.args
    if name == "write_text":
        return len(call.args) < 2
    if name == "fdopen":
        mode = _mode(call, 1, "r")
        return mode is not None and "b" not in mode
    if name in _TEMPORARY:
        mode = _mode(call, 0, "w+b")
        return mode is not None and "b" not in mode
    if name == "open":
        # ``path.open()`` and ``path.open("w")``. A first argument that is
        # not a mode is another library's open (tarfile, urllib, PIL).
        owner = function.value
        if isinstance(owner, ast.Name) and owner.id in _OTHER_OPENS:
            return False
        mode = _mode(call, 0, "r" if not call.args else None)
        return mode is not None and "b" not in mode
    return False


def _runs_text_without_encoding(call: ast.Call) -> bool:
    function = call.func
    name = function.attr if isinstance(function, ast.Attribute) else getattr(function, "id", "")
    if name not in _SUBPROCESS or _keyword(call, "encoding") is not None or _splat(call):
        return False
    for flag in ("text", "universal_newlines"):
        value = _keyword(call, flag)
        if value is not None and not (isinstance(value, ast.Constant) and not value.value):
            return True
    return False


def _is_os_call(call: ast.Call, name: str) -> bool:
    function = call.func
    return (isinstance(function, ast.Attribute) and function.attr == name
            and isinstance(function.value, ast.Name) and function.value.id == "os")


def _replaces_a_file(call: ast.Call) -> bool:
    """``os.replace(a, b)`` or ``path.replace(target)``.

    ``Path.replace`` takes one positional argument and nothing else;
    ``str.replace`` takes two, and ``datetime.replace`` and
    ``dataclasses.replace`` take keywords or a named module.
    """
    function = call.func
    if not isinstance(function, ast.Attribute) or function.attr != "replace":
        return False
    owner = function.value
    if isinstance(owner, ast.Name) and owner.id == "os":
        return True
    if isinstance(owner, ast.Name) and owner.id in _NOT_A_PATH:
        return False
    return len(call.args) == 1 and not call.keywords


def _scopes(tree: ast.AST):
    """Each function with its own nodes, then the module with the rest."""
    functions = [node for node in ast.walk(tree)
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for function in functions:
        yield function
    yield tree


def _flag_sources(source: str, tree: ast.AST) -> dict[int, str]:
    """For each ``os.open`` call, by id, the source of everything that
    builds its flags: the argument, and every assignment to the name it is
    when the flags are built on lines of their own (in the same function,
    or at module level for a constant)."""
    module_names: dict[str, list[str]] = {}
    result: dict[int, str] = {}

    def assignments(scope: ast.AST) -> dict[str, list[str]]:
        names: dict[str, list[str]] = {}
        for node in ast.walk(scope):
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)) and node.value is not None:
                targets = [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    names.setdefault(target.id, []).append(ast.get_source_segment(source, node.value) or "")
        return names

    module_names = assignments(tree)
    for scope in _scopes(tree):
        local = assignments(scope) if scope is not tree else module_names
        for call in _calls(scope):
            if not _is_os_call(call, "open") or id(call) in result:
                continue
            flags = call.args[1] if len(call.args) > 1 else _keyword(call, "flags")
            text = ast.get_source_segment(source, call) or ""
            if isinstance(flags, ast.Name):
                built = local.get(flags.id) or module_names.get(flags.id) or []
                text += " " + " ".join(built)
            result[id(call)] = text
    return result


def _report(found: Counter, known: dict[str, int], what: str) -> None:
    over = {path: count for path, count in found.items() if count > known.get(path, 0)}
    assert not over, f"{what}: {over}"
    stale = {path: count for path, count in known.items() if found.get(path, 0) < count}
    assert not stale, f"fixed; lower or remove these entries: {stale}"


def test_every_text_open_names_its_encoding() -> None:
    found: Counter = Counter()
    for relative, _source, tree in _sources(*PRODUCT):
        found[relative] += sum(_opens_text_without_encoding(call) for call in _calls(tree))
    _report(+found, KNOWN_TEXT_OPENS, 'text opened without encoding="utf-8"')


def test_every_text_subprocess_in_state_names_its_encoding() -> None:
    found: Counter = Counter()
    for relative, _source, tree in _sources("openprogram/state"):
        found[relative] += sum(_runs_text_without_encoding(call) for call in _calls(tree))
    _report(+found, KNOWN_SUBPROCESS_TEXT, 'subprocess text=True without encoding="utf-8"')


def test_files_are_published_through_the_platform_seam() -> None:
    found: Counter = Counter()
    for relative, _source, tree in _sources("openprogram"):
        found[relative] += sum(_replaces_a_file(call) for call in _calls(tree))
    _report(+found, KNOWN_BARE_REPLACE, "os.replace or Path.replace instead of filesystem.replace_file")


def test_the_replace_scan_sees_each_spelling() -> None:
    def count(snippet: str) -> int:
        return sum(_replaces_a_file(call) for call in _calls(ast.parse(snippet)))

    assert count("os.replace(tmp, path)") == 1
    assert count("tmp.replace(path)") == 1
    assert count("Path(tmp).replace(path)") == 1
    assert count("name.replace('a', 'b')") == 0
    assert count("moment.replace(tzinfo=None)") == 0
    assert count("dataclasses.replace(record)") == 0
    assert count("replace_file(tmp, path)") == 0


def _text_descriptors(source: str, tree: ast.AST) -> int:
    """Writable ``os.open`` calls without ``O_BINARY`` in a file that writes
    with ``os.write``. A call with ``dir_fd`` runs on POSIX only."""
    calls = list(_calls(tree))
    if not any(_is_os_call(call, "write") for call in calls):
        return 0
    return sum(
        any(flag in text for flag in _WRITE_FLAGS) and "O_BINARY" not in text and "dir_fd" not in text
        for text in _flag_sources(source, tree).values()
    )


def test_descriptors_written_with_os_write_are_binary() -> None:
    found: Counter = Counter()
    for relative, source, tree in _sources(*PRODUCT):
        found[relative] += _text_descriptors(source, tree)
    _report(+found, KNOWN_TEXT_DESCRIPTORS, "os.open for os.write without O_BINARY")


def test_the_descriptor_scan_follows_flags_built_on_their_own_lines() -> None:
    def count(snippet: str) -> int:
        return _text_descriptors(snippet, ast.parse(snippet))

    separate = (
        "def append(path, data):\n"
        "    flags = os.O_WRONLY | os.O_APPEND\n"
        "    if hasattr(os, 'O_NOFOLLOW'):\n"
        "        flags |= os.O_NOFOLLOW\n"
        "    fd = os.open(path, flags)\n"
        "    os.write(fd, data)\n"
    )
    assert count(separate) == 1
    assert count(separate.replace("os.O_APPEND", "os.O_APPEND | getattr(os, 'O_BINARY', 0)")) == 0
    assert count("FLAGS = os.O_WRONLY\ndef f(p, d):\n    os.write(os.open(p, FLAGS), d)\n") == 1
    assert count("def f(p, d):\n    os.write(os.open(p, os.O_WRONLY | O_BINARY), d)\n") == 0
    assert count("def f(p):\n    return os.open(p, os.O_RDONLY)\ndef g(fd, d):\n    os.write(fd, d)\n") == 0

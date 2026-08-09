"""Validated read-only shell access over an ephemeral memory view."""

import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

from .views import memory_files
from ..workspace_layout import TEMPORARY_PREFIX


_READ_ONLY_COMMANDS = {
    "cat",
    "cut",
    "find",
    "grep",
    "head",
    "ls",
    "pwd",
    "rg",
    "sed",
    "sort",
    "tail",
    "uniq",
    "wc",
}


def validate_read_only_command(command: object) -> tuple[bool, str]:
    if not isinstance(command, str) or not command.strip():
        return False, "empty command"
    if any(marker in command for marker in (
        "\n", "\r", "`", "$", ";", "&&", "||", ">", "<", "(", ")"
    )):
        return False, "shell control or redirection is not allowed"
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False, "command cannot be parsed"
    if not tokens:
        return False, "empty command"
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token == "|":
            if not segments[-1]:
                return False, "empty pipeline segment"
            segments.append([])
        else:
            segments[-1].append(token)
    if not segments[-1]:
        return False, "empty pipeline segment"
    for segment in segments:
        program = segment[0]
        if program not in _READ_ONLY_COMMANDS:
            return False, f"command {program!r} is not read-only"
        for token in segment[1:]:
            if token.startswith(("/", "~")):
                return False, "absolute and home paths are not allowed"
            if re.search(r"(^|/)\.\.($|/)", token):
                return False, "parent-directory traversal is not allowed"
        if program == "find" and any(
            token in {
                "-delete",
                "-exec",
                "-execdir",
                "-ok",
                "-okdir",
                "-fprint",
                "-fprintf",
                "-fls",
            }
            for token in segment[1:]
        ):
            return False, "mutating find actions are not allowed"
        if program == "sed":
            options = [
                token for token in segment[1:] if token.startswith("-")
            ]
            if any(token != "-n" for token in options):
                return False, "only sed -n is allowed"
        if program == "sort" and any(
            token == "-o" or token.startswith("--output")
            for token in segment[1:]
        ):
            return False, "sort output files are not allowed"
    return True, "ok"


def normalize_workspace_command(command: object, memory_dir: Path) -> object:
    if not isinstance(command, str):
        return command
    root = str(memory_dir.resolve())
    return command.replace(f"{root}/", "./").replace(root, ".")


def execute_workspace_bash(
    command: object,
    memory_dir: Path,
) -> str:
    command = normalize_workspace_command(command, memory_dir)
    allowed, reason = validate_read_only_command(command)
    if not allowed:
        return f"Command rejected: {reason}"
    with tempfile.TemporaryDirectory(
        prefix=f"{TEMPORARY_PREFIX}read-"
    ) as temporary:
        view = Path(temporary)
        root = memory_dir.resolve()
        for source in memory_files(root):
            destination = view / source.relative_to(root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        try:
            result = subprocess.run(
                ["/bin/bash", "-c", str(command)],
                cwd=view,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return "Error: command timed out"
    output = f"{result.stdout}{result.stderr}".strip()
    return output or "(no output)"

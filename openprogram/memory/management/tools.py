"""Claude Code MCP tools for transactional memory edits."""

from __future__ import annotations

import glob as _glob
import re
from pathlib import Path
from typing import Any

from .workspace import MemoryWorkspace


def management_tools(
    workspace: MemoryWorkspace,
    audit: list[dict[str, Any]],
) -> list[Any]:
    # Imported here so reading memory does not require the agent SDK:
    # only a run that actually writes needs it.
    from claude_agent_sdk import tool

    def _guidance(command: str, output: str) -> str:
        """What to do about a failure, appended to the raw error.

        A shell error says what went wrong in the shell's terms. Naming the
        tool that does the job turns a retry-the-same-thing loop into one
        corrected call.
        """
        if "No such file or directory" in output and ">" in command:
            return (
                "Redirecting into a path whose directory does not exist fails. "
                "Use the Write tool instead: it creates parent directories and "
                "takes the finished file in one call."
            )
        if "Read-only file system" in output or "Permission denied" in output:
            return (
                "Files under sources/ are the read-only evidence record. The "
                "conversation text is already in your prompt; write the fact "
                "into a Topic file under topics/ instead."
            )
        if "command not found" in output or "syntax error" in output:
            return (
                "This argument has to be an executable command. To create or "
                "change a file's contents, call the Write or Edit tool rather "
                "than describing the change here."
            )
        return ""

    def _result(tool_name: str, arguments: dict[str, Any], operation) -> dict[str, Any]:
        record: dict[str, Any] = {
            "round": len(audit), "tool": tool_name, "arguments": arguments,
        }
        try:
            output = str(operation())
            record.update({"status": "ok", "output": output[:1000]})
        except Exception as exc:
            output = f"{type(exc).__name__}: {exc}"
            record.update({"status": "error", "output": output})
        audit.append(record)
        return {
            "content": [{"type": "text", "text": output or "(no output)"}],
            "is_error": record["status"] == "error",
        }

    def _path(raw: Any, *, writable: bool = False) -> tuple[Path, Path]:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("file_path is required")
        stage_root = workspace.stage_dir.resolve()
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = stage_root / candidate
        resolved = candidate.resolve()
        try:
            relative = resolved.relative_to(stage_root)
        except ValueError as exc:
            raise ValueError("path is outside the managed memory workspace") from exc
        if writable and relative.parts and relative.parts[0].casefold() == "sources":
            raise ValueError("Source Memory is append-only")
        return resolved, relative

    @tool(
        "shell",
        (
            "Run one POSIX shell command in the memory workspace, for things "
            "the file tools cannot do: listing, moving, or removing files. "
            "The argument must be an executable command such as `ls topics`, "
            "never an instruction written in English. To create or change a "
            "file's contents use the Write and Edit tools instead of this one."
        ),
        {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
            "additionalProperties": False,
        },
    )
    async def shell(arguments: dict[str, Any]) -> dict[str, Any]:
        command = str(arguments.get("command", ""))
        record: dict[str, Any] = {
            "round": len(audit),
            "tool": "shell",
            "command": command,
        }
        try:
            result = workspace.shell(command)
            output = f"{result.stdout}{result.stderr}"
            record.update({
                "returncode": result.returncode,
                "output": output,
                "count": workspace.last_created_blocks,
                "topic_paths": workspace.last_changed_topics,
                "status": "ok" if result.returncode == 0 else "error",
            })
        except Exception as exc:  # validation failure becomes an MCP tool error
            output = f"{type(exc).__name__}: {exc}"
            record.update({"status": "error", "output": output})
        audit.append(record)
        if record["status"] == "error":
            advice = _guidance(command, output)
            if advice:
                output = f"{output}\n\n{advice}" if output else advice
        return {
            "content": [{"type": "text", "text": output or "(no output)"}],
            "is_error": record["status"] == "error",
        }

    @tool(
        "Read",
        "Read a UTF-8 text file through OpenProgram's managed workspace boundary.",
        {
            "type": "object",
            "properties": {"file_path": {"type": "string"}},
            "required": ["file_path"],
            "additionalProperties": False,
        },
    )
    async def read_file(arguments: dict[str, Any]) -> dict[str, Any]:
        def operation() -> str:
            path, _ = _path(arguments.get("file_path"))
            if not path.is_file():
                raise ValueError(f"file does not exist: {path}")
            text = path.read_text(encoding="utf-8", errors="replace")
            return text[:200_000] + ("\n[truncated]" if len(text) > 200_000 else "")

        return _result("Read", arguments, operation)

    @tool(
        "Write",
        "Create or replace a UTF-8 file through OpenProgram's managed workspace boundary.",
        {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["file_path", "content"],
            "additionalProperties": False,
        },
    )
    async def write_file(arguments: dict[str, Any]) -> dict[str, Any]:
        def operation() -> str:
            path, relative = _path(arguments.get("file_path"), writable=True)
            content = arguments.get("content")
            if not isinstance(content, str):
                raise ValueError("content must be a string")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return f"Wrote {len(content)} bytes to {relative.as_posix()}"

        return _result("Write", arguments, operation)

    @tool(
        "Edit",
        "Replace exact text in a file through OpenProgram's managed workspace boundary.",
        {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
                "replace_all": {"type": "boolean"},
            },
            "required": ["file_path", "old_string", "new_string"],
            "additionalProperties": False,
        },
    )
    async def edit_file(arguments: dict[str, Any]) -> dict[str, Any]:
        def operation() -> str:
            path, relative = _path(arguments.get("file_path"), writable=True)
            old = arguments.get("old_string")
            new = arguments.get("new_string")
            if not isinstance(old, str) or not isinstance(new, str):
                raise ValueError("old_string and new_string must be strings")
            text = path.read_text(encoding="utf-8")
            count = text.count(old)
            if count == 0:
                raise ValueError("old_string was not found")
            replace_all = bool(arguments.get("replace_all", False))
            if count > 1 and not replace_all:
                raise ValueError(f"old_string occurs {count} times")
            path.write_text(
                text.replace(old, new) if replace_all else text.replace(old, new, 1),
                encoding="utf-8",
            )
            return f"Edited {relative.as_posix()}"

        return _result("Edit", arguments, operation)

    @tool(
        "Glob",
        "List files matching a glob inside the managed memory workspace.",
        {
            "type": "object",
            "properties": {"pattern": {"type": "string"}},
            "required": ["pattern"],
            "additionalProperties": False,
        },
    )
    async def glob_files(arguments: dict[str, Any]) -> dict[str, Any]:
        def operation() -> str:
            pattern = arguments.get("pattern")
            if not isinstance(pattern, str) or not pattern:
                raise ValueError("pattern is required")
            raw = pattern if Path(pattern).is_absolute() else str(
                workspace.stage_dir.resolve() / pattern
            )
            matches: list[str] = []
            for item in _glob.glob(raw, recursive=True):
                try:
                    relative = Path(item).resolve().relative_to(
                        workspace.stage_dir.resolve()
                    )
                except ValueError:
                    continue
                matches.append(relative.as_posix())
                if len(matches) >= 500:
                    break
            return "\n".join(sorted(set(matches))) or "(no matches)"

        return _result("Glob", arguments, operation)

    @tool(
        "Grep",
        "Search UTF-8 files with a regular expression inside the managed workspace.",
        {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
    )
    async def grep_files(arguments: dict[str, Any]) -> dict[str, Any]:
        def operation() -> str:
            pattern = arguments.get("pattern")
            if not isinstance(pattern, str) or not pattern:
                raise ValueError("pattern is required")
            regex = re.compile(pattern)
            root, _ = _path(arguments.get("path") or ".")
            files = [root] if root.is_file() else root.rglob("*")
            matches: list[str] = []
            for path in files:
                if not path.is_file():
                    continue
                try:
                    relative = path.resolve().relative_to(workspace.stage_dir.resolve())
                    lines = path.read_text(
                        encoding="utf-8", errors="replace",
                    ).splitlines()
                except (OSError, ValueError):
                    continue
                for number, line in enumerate(lines, 1):
                    if regex.search(line):
                        matches.append(
                            f"{relative.as_posix()}:{number}:{line[:500]}"
                        )
                        if len(matches) >= 500:
                            return "\n".join(matches)
            return "\n".join(matches) or "(no matches)"

        return _result("Grep", arguments, operation)

    return [
        shell,
        read_file,
        write_file,
        edit_file,
        grep_files,
        glob_files,
    ]


# The writer protocol hash covers the tool surface as well as the prompts, so
# a change to either invalidates a capacity calibration measured against it.
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "shell",
            "description": (
                "Run one POSIX shell command in the memory workspace, for "
                "things the file tools cannot do: listing, moving, or "
                "removing files."
            ),
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    *[
        {"type": "function", "function": {"name": name}}
        for name in ("Read", "Write", "Edit", "Grep", "Glob")
    ],
]

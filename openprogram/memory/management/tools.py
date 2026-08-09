"""Claude Code MCP tools for transactional memory edits."""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from .workspace import MemoryWorkspace


def management_tools(
    workspace: MemoryWorkspace,
    audit: list[dict[str, Any]],
) -> list[Any]:
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

    return [shell]


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
]

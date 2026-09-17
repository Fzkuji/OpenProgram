"""Prompt text for the bash tool (description shown to the LLM).

The description follows the one-shot backend contract. It must not promise
persistent shell state; dedicated managed sessions have a different lifecycle.
"""

from __future__ import annotations

DEFAULT_MAX_TIMEOUT_MS = 10 * 60 * 1000   # 10 min
DEFAULT_TIMEOUT_MS = 2 * 60 * 1000        # 2 min

DESCRIPTION = (
    "Execute a host shell command and return its starting directory, stdout, "
    "stderr, and exit code. macOS/Linux use their shell; Windows uses Git Bash "
    "when available and otherwise Windows PowerShell.\n"
    "\n"
    "Each call starts a new shell subprocess. Use workdir to choose the "
    "starting directory for this call only. With workdir omitted, use the "
    "currently bound agent worktree directory, or the active backend's default "
    "working directory when no worktree is bound. Relative workdir paths are "
    "resolved from that worktree (local process directory when unbound), not "
    "from the previous command. Paths are literal: no ~ or variable expansion. "
    "Directory changes (`cd`) and shell state (exported variables, aliases) "
    "do not persist between calls. workdir does not change file-tool paths "
    "or grant new filesystem permissions. Invalid or denied directories fail "
    "without executing the command.\n"
    "\n"
    "- Prefer workdir over cd'ing around. Change directories in the same call "
    "as the commands that need them if a shell-specific sequence requires cd.\n"
    "- SSH/Docker workdir uses POSIX paths; a relative path requires a bound "
    "POSIX worktree. Explicit remote workdir is refused under a host sandbox "
    "policy until backend-native path authorization is available.\n"
    "- Prefer dedicated file/search tools or Python for portable file and text "
    "operations. Avoid assuming Unix coreutils exist on Windows.\n"
    "- For Word/PPT and other binary documents, write the completed output to a "
    "staged ordinary local file, then publish it with `write(file_path=..., "
    "source_path=...)`. Read the existing target first; `content` and `source_path` "
    "cannot be used together.\n"
    "- Emit independent commands as parallel tool calls in one turn. When "
    "chaining is necessary, use syntax supported by the active host shell.\n"
    "- To wait on a process, poll with a check command rather than sleep.\n"
    "- For git, prefer new commits over amending, and never skip hooks "
    "(--no-verify) unless explicitly asked.\n"
)

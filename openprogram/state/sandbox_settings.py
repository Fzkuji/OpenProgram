"""The sandbox's settings vocabulary: its modes, what it does when no sandbox
can run, and the default read and write denials.

The settings schema (``state.settings``) lists these as the ``sandbox.*``
settings' choices and defaults; the sandbox (``host.sandbox``) reads the
stored values against them when it wraps a command.
"""
from __future__ import annotations

MODE_DANGER_FULL_ACCESS = "danger-full-access"
MODE_WORKSPACE_WRITE = "workspace-write"
MODE_AUTO = "auto"
# The default: no sandbox unless a conversation turns its Sandbox switch
# on (the composer's Plus menu), which then uses the backend when it is
# available.
MODE_OFF = "off"
MODES = (MODE_OFF, MODE_AUTO, MODE_DANGER_FULL_ACCESS, MODE_WORKSPACE_WRITE)

UNAVAILABLE_POLICY_REFUSE = "refuse"
UNAVAILABLE_POLICY_WARN = "warn"
UNAVAILABLE_POLICY = (UNAVAILABLE_POLICY_REFUSE, UNAVAILABLE_POLICY_WARN)

# Loaded, not just present. Both reference harnesses ship a deny-read
# engine with an empty list and close the loop on egress instead; that
# reasoning does not carry here, because the memory writer is an egress
# channel that never touches the network — anything it writes returns to
# a later session's context.
DEFAULT_DENY_READ: tuple[str, ...] = (
    "~/.ssh/**",
    "~/.aws/**",
    "~/.gnupg/**",
    "~/.openprogram/auth/**",
    "~/.claude.json",
    "~/.claude/.credentials.json",
    "~/.config/gh/**",
    "~/.netrc",
    "~/Library/Keychains/**",
    "**/.env",
)

# Extra write denials a user configures. Empty by default: the escapes of
# this shape (code that runs outside the sandbox later) are covered by
# ``protected_writes``, which no config can remove. Git hooks and config are
# covered only for the repository the command starts in, because `git init` and
# `git clone` write `.git/hooks/` of a new one.
DEFAULT_DENY_WRITE: tuple[str, ...] = ()


__all__ = [
    "DEFAULT_DENY_READ",
    "DEFAULT_DENY_WRITE",
    "MODES",
    "MODE_AUTO",
    "MODE_DANGER_FULL_ACCESS",
    "MODE_OFF",
    "MODE_WORKSPACE_WRITE",
    "UNAVAILABLE_POLICY",
    "UNAVAILABLE_POLICY_REFUSE",
    "UNAVAILABLE_POLICY_WARN",
]

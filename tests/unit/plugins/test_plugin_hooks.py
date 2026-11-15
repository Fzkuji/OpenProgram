"""A plugin's hooks are shell commands declared in its manifest (plugins/hooks.py)."""
from __future__ import annotations

from openprogram.extensions.plugins.hooks import CommandHook, command_hooks


def test_openprogram_shape_reads_event_and_command():
    assert command_hooks({"tool.after": [{"command": "log.sh", "timeout": 5}]}) == [
        CommandHook("tool.after", "log.sh", 5.0, "")]


def test_claude_code_shape_maps_events_matchers_and_the_plugin_root():
    value = {"hooks": {
        "PreToolUse": [{"matcher": "Bash|Write", "hooks": [
            {"type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/check.sh", "timeout": 9}]}],
        "Stop": [{"hooks": [{"type": "command", "command": "done.sh"}]}],
    }}
    assert command_hooks(value, "/plugins/demo") == [
        CommandHook("tool.before", "/plugins/demo/check.sh", 9.0, "Bash|Write"),
        CommandHook("turn.stop", "done.sh", 60.0, ""),
    ]


def test_unknown_events_and_non_command_hooks_are_skipped():
    value = {"Nope": [{"command": "x"}], "PostToolUse": [{"hooks": [{"type": "prompt", "prompt": "p"}]}]}
    assert command_hooks(value) == []
    assert command_hooks("not a mapping") == []


def test_matcher_selects_tools(tmp_path):
    from openprogram.runtime.events import make_event
    from openprogram.extensions.plugins.hooks import _matches
    hook = CommandHook("tool.before", "x", 1.0, "Bash|Write")
    assert _matches(hook, make_event("tool.before", "tool", {"tool": "Write"}))
    assert not _matches(hook, make_event("tool.before", "tool", {"tool": "read"}))
    assert _matches(CommandHook("tool.before", "x"), make_event("tool.before", "tool", {"tool": "read"}))

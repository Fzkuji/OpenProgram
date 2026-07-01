"""权限规则字符串的解析、序列化、匹配。

见 docs/design/runtime/permission-model.md §2.2 / §3.4。

规则字符串语法：
    ToolName                 整工具（per-tool）。例：bash / write_file
    ToolName(content)        命令级（per-pattern）。例：bash(git:*) / read_file(/etc/**)

content 内的 ``(`` ``)`` ``\\`` 需转义（``\\( \\) \\\\``），因为它们是语法定界符。
parse_rule / rule_to_string 互为对偶。
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class PermissionRuleValue:
    tool_name: str
    pattern: Optional[str] = None   # None = per-tool；非 None = per-pattern


def _unescape(s: str) -> str:
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            out.append(s[i + 1])
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def parse_rule(s: str) -> PermissionRuleValue:
    """``bash`` → (bash, None)；``bash(git:*)`` → (bash, "git:*")。
    尾部必须是未转义的 ``)``；找第一个未转义的 ``(`` 作为 pattern 起点。"""
    s = s.strip()
    if not s.endswith(")") or _is_escaped(s, len(s) - 1):
        return PermissionRuleValue(tool_name=s)
    # 找第一个未转义的 "("
    open_idx = -1
    i = 0
    while i < len(s):
        if s[i] == "\\":
            i += 2
            continue
        if s[i] == "(":
            open_idx = i
            break
        i += 1
    if open_idx < 0:
        return PermissionRuleValue(tool_name=s)
    tool = s[:open_idx].strip()
    raw_pattern = s[open_idx + 1:len(s) - 1]
    return PermissionRuleValue(tool_name=tool, pattern=_unescape(raw_pattern))


def _is_escaped(s: str, idx: int) -> bool:
    """idx 处字符前有奇数个反斜杠则被转义。"""
    n = 0
    j = idx - 1
    while j >= 0 and s[j] == "\\":
        n += 1
        j -= 1
    return n % 2 == 1


def rule_to_string(v: PermissionRuleValue) -> str:
    if v.pattern is None:
        return v.tool_name
    return f"{v.tool_name}({_escape(v.pattern)})"


def parse_command(tool_name: str, args: dict) -> Optional[str]:
    """把工具参数归约成一个可比字符串（per-pattern 匹配用）。
    - bash / exec / shell → args["command"]
    - read* / write* / edit* → args["path"]（或 file_path）
    - 其余工具无可比字段 → None（per-pattern 对其不生效，只 per-tool 可拦）。"""
    if not isinstance(args, dict):
        return None
    low = tool_name.lower()
    if low in {"bash", "exec", "shell", "execute_code", "process"}:
        cmd = args.get("command")
        return str(cmd) if cmd is not None else None
    if any(k in low for k in ("read", "write", "edit", "apply_patch", "list")):
        p = args.get("path") or args.get("file_path")
        return str(p) if p is not None else None
    return None


def pattern_matches(pattern: str, value: str) -> bool:
    """per-pattern 匹配规则：
    - ``prefix:*`` → 前缀匹配（``git:*`` 匹配 "git status"、不匹配 "github"）。
    - 含 glob 元字符（``*?[``）→ fnmatch（``/etc/**`` 匹配 "/etc/passwd"）。
    - 否则 → 精确相等。"""
    if pattern.endswith(":*"):
        prefix = pattern[:-2]
        return value == prefix or value.startswith(prefix + " ")
    if any(ch in pattern for ch in "*?["):
        return fnmatch.fnmatch(value, pattern)
    return value == pattern

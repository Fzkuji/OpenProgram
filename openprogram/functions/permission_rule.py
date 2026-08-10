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
import json
import os
import re
import shlex
import unicodedata
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class PermissionRuleValue:
    tool_name: str
    pattern: Optional[str] = None   # None = per-tool；非 None = per-pattern


_SHELL_TOOLS = {"bash", "exec", "shell"}
_COMPLEX_SHELL_PREFIX = "\x1fcomplex-shell:"
_ANSI_ESCAPE_RE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|[@-Z\\-_])"
)
_ASSIGNMENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*", re.DOTALL)
_ENV_NO_ARG = {"-i", "--ignore-environment", "-0", "--null"}
_ENV_WITH_ARG = {"-u", "--unset", "-C", "--chdir", "--argv0"}
_SHELL_OPERATORS = {";", "&", "&&", "|", "||", "(", ")", "<", ">", "<<", ">>"}


def _normalize_text(value: object) -> str:
    text = _ANSI_ESCAPE_RE.sub("", str(value)).replace("\x00", "")
    return unicodedata.normalize("NFKC", text)


def _shell_tokens(command: str) -> tuple[list[str], bool]:
    """Return normalized shell tokens and whether shell interpretation is complex."""
    complex_syntax = "\n" in command or "\r" in command or "`" in command
    try:
        lexer = shlex.shlex(
            command, posix=True, punctuation_chars="();<>|&",
        )
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return [], True
    if any(
        token in _SHELL_OPERATORS
        or "$" in token
        or any(char in token for char in "*?[")
        or token.startswith("~")
        or token.startswith("#")
        for token in tokens
    ):
        complex_syntax = True
    return tokens, complex_syntax


def _canonical_shell(command: object) -> str:
    normalized = _normalize_text(command).strip()
    tokens, complex_syntax = _shell_tokens(normalized)
    if complex_syntax or not tokens:
        return _COMPLEX_SHELL_PREFIX + normalized
    return shlex.join(tokens)


def _effective_shell_command(value: str) -> str | None:
    """Remove transparent assignments and ``env`` wrapping for prefix rules."""
    if value.startswith(_COMPLEX_SHELL_PREFIX):
        return None
    try:
        tokens = shlex.split(value, posix=True)
    except ValueError:
        return None
    while tokens and _ASSIGNMENT_RE.fullmatch(tokens[0]):
        tokens.pop(0)
    if not tokens or tokens[0] != "env":
        return shlex.join(tokens) if tokens else None

    tokens.pop(0)
    while tokens:
        token = tokens[0]
        if token == "--":
            tokens.pop(0)
            break
        if _ASSIGNMENT_RE.fullmatch(token) or token in _ENV_NO_ARG \
                or token.startswith("--unset=") \
                or token.startswith("--chdir=") \
                or token.startswith("--argv0="):
            tokens.pop(0)
            continue
        if token in _ENV_WITH_ARG:
            if len(tokens) < 2:
                return None
            del tokens[:2]
            continue
        if token == "-S" or token.startswith("--split-string="):
            return None
        if token.startswith("-"):
            return None
        break
    return shlex.join(tokens) if tokens else None


def _normalize_json(value):
    if isinstance(value, str):
        return _normalize_text(value)
    if isinstance(value, dict):
        return {
            _normalize_text(key): _normalize_json(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_json(item) for item in value]
    return value


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
    - bash / exec / shell → normalized args["command"]
    - read* / write* / edit* → args["path"]（或 file_path）
    - 其余工具 → canonical JSON，避免精确批准退化成整工具批准。"""
    if not isinstance(args, dict):
        return None
    low = tool_name.lower()
    if low in _SHELL_TOOLS:
        cmd = args.get("command")
        return _canonical_shell(cmd) if cmd is not None else None
    if any(k in low for k in ("read", "write", "edit", "apply_patch", "list")):
        p = args.get("path") or args.get("file_path")
        if p is not None:
            normalized = _normalize_text(p)
            return os.path.normpath(normalized) if normalized else normalized
    try:
        return json.dumps(
            _normalize_json(args), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), default=str,
        )
    except (TypeError, ValueError):
        return _normalize_text(args)


def exact_rule_for_call(tool_name: str, args: dict) -> PermissionRuleValue | None:
    """Build the narrow persistent rule for one transparent operation."""
    value = parse_command(tool_name, args)
    if value is None:
        return None
    if tool_name.lower() in _SHELL_TOOLS and value.startswith(_COMPLEX_SHELL_PREFIX):
        return None
    return PermissionRuleValue(tool_name=tool_name, pattern=value)


def load_merged_rules(session_id: str):
    """合并各来源的权限规则，返回 PermissionRules（低→高：global < session）。
    见 docs/design/runtime/permission-model.md §2.3。

    落地三层真实载体——全局配置 < 项目（主要载体）< 会话（临时覆盖）。
    权限规则跟着**项目**走（<project>/.openprogram/settings.json），所以切会话
    规则还在、"总是允许"能长期记住。会话层保留作最高优先的一次性覆盖。
    合并只是拼接三 list：deny/ask/allow 的总序由 _match_rule 保证（命中即返回），
    来源顺序只影响同一 behavior 内的先后。见 permission-model.md §2.3。"""
    from openprogram.agent.session_config import PermissionRules, load_session_run_config

    merged = PermissionRules()

    def _extend(src: dict | None):
        if not src:
            return
        for k in ("allow", "deny", "ask"):
            getattr(merged, k).extend(str(v) for v in (src.get(k) or []) if str(v))

    # 全局层（最低）
    try:
        from openprogram.webui import _setup
        cfg = _setup._read_config() or {}
        _extend(((cfg.get("tools") or {}).get("permission_rules")) or {})
    except Exception:
        pass

    # 项目层（主要载体）——session → project → project settings 的 permission_rules
    try:
        from openprogram.store.project import project_store as _projects
        proj = _projects.project_for_session(session_id)
        if proj is not None:
            _extend(_projects.load_project_settings(proj.id).get("permission_rules"))
    except Exception:
        pass

    # 会话层（最高优先，一次性覆盖）
    try:
        sess = load_session_run_config(session_id).permission_rules
        if sess is not None:
            merged.allow.extend(sess.allow)
            merged.deny.extend(sess.deny)
            merged.ask.extend(sess.ask)
    except Exception:
        pass

    return merged


def pattern_matches(pattern: str, value: str) -> bool:
    """per-pattern 匹配规则：
    - ``prefix:*`` → 前缀匹配（``git:*`` 匹配 "git status"、不匹配 "github"）。
    - 含 glob 元字符（``*?[``）→ fnmatch（``/etc/**`` 匹配 "/etc/passwd"）。
    - 否则 → 精确相等。"""
    pattern = _normalize_text(pattern)
    if pattern.endswith(":*"):
        prefix = pattern[:-2]
        candidate = _effective_shell_command(value)
        if candidate is None:
            return False
        return candidate == prefix or candidate.startswith(prefix + " ")
    if any(ch in pattern for ch in "*?["):
        return fnmatch.fnmatch(value, pattern)
    return value == pattern

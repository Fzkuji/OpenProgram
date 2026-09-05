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
import logging
import os
import re
import shlex
import unicodedata
from dataclasses import dataclass
from typing import Optional

_log = logging.getLogger(__name__)


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
_RESOLUTION_ENV = frozenset({
    "PATH",
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "DYLD_INSERT_LIBRARIES",
    "DYLD_LIBRARY_PATH",
    "DYLD_FRAMEWORK_PATH",
})
_ENV_NO_ARG = {"-i", "--ignore-environment", "-0", "--null"}
_ENV_WITH_ARG = {"-u", "--unset", "-C", "--chdir", "--argv0"}
_SHELL_OPERATORS = {";", "&", "&&", "|", "||", "(", ")", "<", ">", "<<", ">>"}
_PATH_TOOLS = frozenset({
    "read", "read_file",
    "write", "write_file",
    "edit", "edit_file",
    "list", "list_dir",
    "apply_patch",
})
_PATCH_FILE_PREFIXES = (
    "*** Add File: ",
    "*** Update File: ",
    "*** Delete File: ",
)
_PATH_SEP = "\x1e"


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


def _assignment_name(token: str) -> str | None:
    if _ASSIGNMENT_RE.fullmatch(token):
        return token.split("=", 1)[0]
    return None


def _effective_shell_command(value: str, *, allow: bool = False) -> str | None:
    """Remove transparent assignments and ``env`` wrapping for prefix rules.

    When ``allow`` is true, stripped assignments that change executable
    resolution (PATH, LD_PRELOAD, DYLD_INSERT_LIBRARIES, ...) yield no
    match. Deny matching keeps ``allow`` false so those commands still hit
    deny rules.
    """
    if value.startswith(_COMPLEX_SHELL_PREFIX):
        return None
    try:
        tokens = shlex.split(value, posix=True)
    except ValueError:
        return None
    touched_resolution = False
    while tokens:
        name = _assignment_name(tokens[0])
        if name is None:
            break
        if name in _RESOLUTION_ENV:
            touched_resolution = True
        tokens.pop(0)
    if not tokens or tokens[0] != "env":
        if not tokens or (allow and touched_resolution):
            return None
        return shlex.join(tokens)

    tokens.pop(0)
    while tokens:
        token = tokens[0]
        if token == "--":
            tokens.pop(0)
            break
        name = _assignment_name(token)
        if name is not None or token in _ENV_NO_ARG \
                or token.startswith("--unset=") \
                or token.startswith("--chdir=") \
                or token.startswith("--argv0="):
            if name in _RESOLUTION_ENV:
                touched_resolution = True
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
    if not tokens or (allow and touched_resolution):
        return None
    return shlex.join(tokens)


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


def _canon_path(path: object) -> str:
    normalized = _normalize_text(path)
    if not normalized:
        return normalized
    # Only absolute paths resolve here. Relative paths must stay relative:
    # callers (_path_is_safe, _targets_agentics) anchor them to the session
    # worktree, not the process cwd that realpath would use.
    # Python 3.13 deliberately stopped treating a single leading slash as
    # absolute on Windows. Model/tool calls still commonly use POSIX-style
    # ``/tmp/x`` paths there (Git Bash accepts them), so preserve the prior
    # drive-root interpretation instead of turning them into ``\tmp\x``.
    if os.path.isabs(normalized) or (
        os.name == "nt" and normalized.startswith(("/", "\\"))
    ):
        return os.path.realpath(normalized)
    return os.path.normpath(normalized)


def _patch_paths(patch: object) -> list[str]:
    if not isinstance(patch, str):
        return []
    paths: list[str] = []
    for line in patch.splitlines():
        for prefix in _PATCH_FILE_PREFIXES:
            if line.startswith(prefix):
                raw = line[len(prefix):].strip()
                if raw:
                    paths.append(_canon_path(raw))
                break
    return paths


def _realpath_glob_pattern(pattern: str) -> str:
    idx = next((i for i, ch in enumerate(pattern) if ch in "*?["), None)
    if idx is None:
        return os.path.realpath(pattern) if pattern else pattern
    head, tail = pattern[:idx], pattern[idx:]
    if not head:
        return pattern
    if head.endswith(("/", os.sep)):
        root = os.path.realpath(head.rstrip("/\\") or "/")
        # The canonical value uses the native separator. A mixed
        # ``C:\etc/**`` pattern never matches ``C:\etc\passwd`` on Windows.
        return root.rstrip("/\\") + os.sep + tail
    parent, base = os.path.split(head)
    if not parent:
        return pattern
    return os.path.join(os.path.realpath(parent), base) + tail


def parse_command(tool_name: str, args: dict) -> Optional[str]:
    """把工具参数归约成一个可比字符串（per-pattern 匹配用）。
    - bash / exec / shell → normalized args["command"]
    - 路径工具 → realpath(args["path"] 或 file_path)
    - apply_patch → patch 文本里每个目标文件的 realpath
    - 其余工具 → canonical JSON，避免精确批准退化成整工具批准。"""
    if not isinstance(args, dict):
        return None
    low = tool_name.lower()
    if low in _SHELL_TOOLS:
        cmd = args.get("command")
        return _canonical_shell(cmd) if cmd is not None else None
    if low == "apply_patch":
        paths = _patch_paths(args.get("patch"))
        if paths:
            return _PATH_SEP.join(paths)
    if low in _PATH_TOOLS:
        p = args.get("path") or args.get("file_path")
        if p is not None:
            return _canon_path(p)
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
        if not isinstance(src, dict):
            raise ValueError("permission_rules must be an object")
        for k in ("allow", "deny", "ask"):
            vals = src.get(k) or []
            if not isinstance(vals, (list, tuple)):
                raise ValueError(f"permission_rules.{k} must be a list")
            getattr(merged, k).extend(str(v) for v in vals if str(v))

    # 全局层（最低）。配置缺失是常态；读到了内容却解析失败则抛错。
    try:
        from openprogram.webui import _setup
        cfg = _setup._read_config()
    except Exception as exc:
        _log.debug("permission rules: global config unavailable: %s", exc)
        cfg = None
    else:
        raw = ((cfg or {}).get("tools") or {}).get("permission_rules")
        if raw is not None:
            try:
                _extend(raw)
            except Exception:
                _log.warning("permission rules: global permission_rules unusable")
                raise

    # 项目层（主要载体）——session → project → project settings 的 permission_rules
    try:
        from openprogram.store.project import project_store as _projects
        proj = _projects.project_for_session(session_id)
    except Exception as exc:
        _log.debug("permission rules: project lookup failed: %s", exc)
        proj = None
    if proj is not None:
        try:
            settings = _projects.load_project_settings(proj.id)
            raw = settings.get("permission_rules") if isinstance(settings, dict) else None
            if settings is not None and not isinstance(settings, dict):
                raise ValueError("project settings must be an object")
            if raw is not None:
                _extend(raw)
        except Exception:
            _log.warning(
                "permission rules: project permission_rules unusable for session %s",
                session_id,
            )
            raise

    # 会话层（最高优先，一次性覆盖）
    try:
        sess = load_session_run_config(session_id).permission_rules
    except Exception as exc:
        _log.debug("permission rules: session config unavailable: %s", exc)
        sess = None
    else:
        if sess is not None:
            try:
                merged.allow.extend(sess.allow)
                merged.deny.extend(sess.deny)
                merged.ask.extend(sess.ask)
            except Exception:
                _log.warning(
                    "permission rules: session permission_rules unusable for session %s",
                    session_id,
                )
                raise

    return merged


def _match_one(pattern: str, value: str, *, allow: bool) -> bool:
    if pattern.endswith(":*"):
        prefix = pattern[:-2]
        candidate = _effective_shell_command(value, allow=allow)
        if candidate is None:
            return False
        return candidate == prefix or candidate.startswith(prefix + " ")

    def _cmp(pat: str, val: str) -> bool:
        if any(ch in pat for ch in "*?["):
            # fnmatch() applies os.path.normcase() and therefore makes allow
            # rules case-insensitive on Windows. Allows stay exact/case-aware;
            # deny matching performs its own explicit casefold below.
            return fnmatch.fnmatchcase(val, pat)
        return val == pat

    if allow:
        return _cmp(pattern, value)

    seen: set[tuple[str, str]] = set()
    for pat in (pattern, _realpath_glob_pattern(pattern)):
        for val in (value, os.path.realpath(value) if value else value):
            key = (pat.casefold(), val.casefold())
            if key in seen:
                continue
            seen.add(key)
            if _cmp(*key):
                return True
    return False


def pattern_matches(pattern: str, value: str, *, allow: bool = False) -> bool:
    """per-pattern 匹配规则：
    - ``prefix:*`` → 前缀匹配（``git:*`` 匹配 "git status"、不匹配 "github"）。
    - 含 glob 元字符（``*?[``）→ fnmatch（``/etc/**`` 匹配 "/etc/passwd"）。
    - 否则 → 精确相等。
    deny 对路径做 realpath + casefold（宽松命中）；allow 保持原样匹配。"""
    pattern = _normalize_text(pattern)
    parts = [p for p in value.split(_PATH_SEP) if p] if _PATH_SEP in value else [value]
    if allow:
        return all(_match_one(pattern, part, allow=True) for part in parts)
    return any(_match_one(pattern, part, allow=False) for part in parts)

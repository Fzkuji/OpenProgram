"""权限规则字符串的解析、序列化、匹配。

见 docs/reference/design/runtime/security/sandbox-architecture.html。

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
# A command for PowerShell that uses syntax the POSIX reading below does not
# see (``{}`` script blocks and hash tables, ``,`` arrays). The value keeps the
# command's own text: deny and ask rules read it as usual, and an allow rule
# matches it only when the rule is that exact text ("Always allow").
_POWERSHELL_UNREAD_PREFIX = "\x1fpowershell-unread:"
_POWERSHELL_UNREAD_CHARS = frozenset("{},")
# Where commands run in PowerShell the value also carries the command lowered
# to POSIX words (``powershell_words``), each after this separator. Deny and
# ask rules read every reading; allow rules read only the command as written.
_POWERSHELL_READING = "\x1d"
# The reading of a command ``powershell_words`` cannot read that may still
# delete, move or copy files: a deny rule for those reads it as ask.
_POWERSHELL_UNKNOWN = "\x1fpowershell-unknown"
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


def _runs_in_powershell() -> bool:
    """Whether a command may run in PowerShell: the shell commands run in
    now, or the host shell an approved command leaves the sandbox for."""
    try:
        from openprogram.host._platform import command_shell
        from openprogram.host.environments import current_environment
        profile = command_shell.resolve_shell(current_environment())
    except Exception:  # noqa: BLE001 - unknown is read as the riskier shell
        import sys
        return sys.platform == "win32"
    return profile.is_powershell or (profile.host is not None and profile.host.is_powershell)


def _canonical_shell(command: object) -> str:
    normalized = _normalize_text(command).strip()
    tokens, complex_syntax = _shell_tokens(normalized)
    powershell = _runs_in_powershell()
    if complex_syntax or not tokens:
        base = _COMPLEX_SHELL_PREFIX + normalized
    elif any(char in _POWERSHELL_UNREAD_CHARS for char in normalized) and powershell:
        base = _POWERSHELL_UNREAD_PREFIX + normalized
    else:
        base = shlex.join(tokens)
    if not powershell or not normalized:
        return base
    from openprogram.runtime.permissions import powershell_words
    readings = powershell_words.posix_readings(normalized)
    if readings is None:
        return base + _POWERSHELL_READING + _POWERSHELL_UNKNOWN if powershell_words.may_run(normalized) else base
    lowered = [shlex.join(argv) for argv in readings]
    extra = [text for text in lowered if text != base]
    return base + "".join(_POWERSHELL_READING + text for text in extra)


def _split_readings(value: str) -> tuple[str, list[str]]:
    """The command as written, and its PowerShell readings for deny and ask."""
    if _POWERSHELL_READING not in value:
        return value, []
    base, *readings = value.split(_POWERSHELL_READING)
    return base, readings


def unread_powershell_targets(pattern: str, value: str) -> bool:
    """Whether a deny rule ``pattern`` must ask about a PowerShell command it cannot read.

    True when the command could not be lowered yet may delete, move or copy
    (``powershell_words.may_run``), and the rule names one of those programs.
    A rule with no such program leaves the command alone, so an owner who
    wrote no delete rule is asked nothing new.
    """
    if not value.endswith(_POWERSHELL_READING + _POWERSHELL_UNKNOWN):
        return False
    from openprogram.runtime.permissions import powershell_words
    words = _normalize_text(pattern).replace(":*", " ").split()
    return bool(words) and powershell_words.targets(words[0])


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
    # ``rule_to_string`` doubles every backslash, so its output never has one
    # in front of an ordinary character. A pattern that does was typed or
    # pasted as a plain Windows path, where a doubled backslash is the start
    # of a UNC name (two backslashes, server, share) and must stay doubled.
    typed = any(
        c == "\\" and (i + 1 >= len(s) or s[i + 1] not in "\\()")
        and not (i and s[i - 1] == "\\" and _is_escaped(s, i))
        for i, c in enumerate(s)
    )
    unescaped = "()" if typed else "\\()"
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        # Only the rule grammar's own metacharacters are escaped. Consuming
        # arbitrary backslashes corrupts pasted Windows paths (e.g. C:\Users).
        if c == "\\" and i + 1 < len(s) and s[i + 1] in unescaped:
            out.append(s[i + 1])
            i += 2
        elif typed and c == "\\" and s[i + 1:i + 2] == "\\":
            out.append("\\\\")
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


_WINDOWS_DRIVE_RE = re.compile(r"[A-Za-z]:")


def _windows_identity(path: str, *, literal: bool = False) -> str:
    r"""One spelling for the names Windows treats as the same file.

    ``\\?\C:\x`` and ``\\.\C:\x`` open ``C:\x``, and ``\\?\UNC\srv\share``
    opens ``\\srv\share``; ``realpath`` keeps the prefix it was given, so a
    rule written without it would never see the call. Win32 also drops
    trailing dots and spaces from a component and reads ``name::$DATA`` as
    ``name``. A ``\\?\`` path skips that trimming (``literal``): there the
    trailing dot is part of the name.
    """
    for prefix, replacement in (("\\\\?\\UNC\\", "\\\\"), ("\\\\?\\", ""), ("\\\\.\\", "")):
        if path[:len(prefix)].upper() == prefix:
            rest = path[len(prefix):]
            if replacement or _WINDOWS_DRIVE_RE.match(rest):
                path = replacement + rest
            break
    if literal:
        return path
    drive, tail = os.path.splitdrive(path)
    parts = tail.split("\\")
    for index, part in enumerate(parts):
        if part in ("", ".", "..") or any(ch in part for ch in "*?["):
            continue
        if index == len(parts) - 1 and part.upper().endswith("::$DATA"):
            part = part[:-len("::$DATA")]
        parts[index] = part.rstrip(". ") or part
    return drive + "\\".join(parts)


def _is_absolute(path: str) -> bool:
    # Python 3.13 deliberately stopped treating a single leading slash as
    # absolute on Windows. Model/tool calls still commonly use POSIX-style
    # ``/tmp/x`` paths there (Git Bash accepts them), so preserve the prior
    # drive-root interpretation instead of turning them into ``\tmp\x``.
    return os.path.isabs(path) or (
        os.name == "nt" and path.startswith(("/", "\\"))
    )


def _canon_path(path: object) -> str:
    normalized = _normalize_text(path)
    if not normalized:
        return normalized
    # Only absolute paths resolve here. Relative paths must stay relative:
    # callers (_path_is_safe, _targets_agentics) anchor them to the session
    # worktree, not the process cwd that realpath would use.
    if _is_absolute(normalized):
        resolved = os.path.realpath(normalized)
        if os.name == "nt":
            resolved = _windows_identity(
                resolved, literal=normalized.startswith("\\\\?\\"),
            )
        return resolved
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
        return _resolved(pattern) if pattern else pattern
    head, tail = pattern[:idx], pattern[idx:]
    if not head:
        return pattern
    if head.endswith(("/", os.sep)):
        root = _resolved(head.rstrip("/\\") or "/")
        # The canonical value uses the native separator. A mixed
        # ``C:\etc/**`` pattern never matches ``C:\etc\passwd`` on Windows.
        return root.rstrip("/\\") + os.sep + tail
    parent, base = os.path.split(head)
    if not parent:
        return pattern
    return os.path.join(_resolved(parent), base) + tail


def _resolved(path: str) -> str:
    resolved = os.path.realpath(path)
    if os.name == "nt":
        resolved = _windows_identity(resolved, literal=path.startswith("\\\\?\\"))
    return resolved


def _deny_values(value: str) -> list[str]:
    """Every name a deny or ask rule must see for one call value.

    A file tool resolves a relative path against the conversation's working
    directory, so that is where the rule looks too; the process directory
    would let ``secret/a.txt`` pass a rule written for the absolute folder.
    """
    if not value:
        return [value]
    anchored = value
    if not _is_absolute(value):
        from openprogram.state.worktree.context import current_worktree_path
        base = current_worktree_path()
        if base:
            anchored = os.path.join(base, value)
    values = [value, _resolved(anchored)]
    if os.name == "nt":
        # A rule on a file covers its alternate data streams.
        for item in list(values):
            head, name = os.path.split(item)
            if ":" in name:
                values.append(os.path.join(head, name.split(":", 1)[0]))
    return values


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
    if low in {"computer_use_agent", "computer_use"}:
        # One approval covers an application, not one click in it: the rule
        # names the app the model addressed (``computer_use_agent(calculator)``),
        # the VM endpoint (``computer_use_agent(vm:http://host:5000)``), or
        # the user's own browser (``computer_use_agent(browser:chrome)``).
        target = args.get("target", "app")
        if target == "vm":
            from openprogram.programs.tools.computer_use.computer_use_agent.targets import vm
            return f"vm:{vm.url()}" if vm.url() else None
        nested = args.get("args") if isinstance(args.get("args"), dict) else args
        if target == "browser":
            # The user's own Chrome or Edge is one grant for everything done
            # in it, page scripts included; built-in Pages have no rule.
            from openprogram.programs._host import services as _services
            browser = _services.lookup("browser")
            chosen = browser.browser_of(nested) if browser is not None else ""
            return f"browser:{chosen}" if chosen else None
        if target != "app":
            return None
        app = " ".join(str(nested.get("app") or "").split()).lower()
        return app or None
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
    value = _split_readings(value)[0]
    if tool_name.lower() in _SHELL_TOOLS and value.startswith(_COMPLEX_SHELL_PREFIX):
        return None
    if value.startswith(_POWERSHELL_UNREAD_PREFIX):
        value = value[len(_POWERSHELL_UNREAD_PREFIX):]
    return PermissionRuleValue(tool_name=tool_name, pattern=value)


def load_merged_rules(session_id: str, *, branch_id: str | None = None):
    """合并各来源的权限规则，返回 PermissionRules（低→高：global < session）。
    见 docs/reference/design/runtime/security/permission-decision.html。

    落地三层真实载体——全局配置 < 项目（主要载体）< 会话（临时覆盖）。
    权限规则跟着**项目**走（<project>/.openprogram/settings.json），所以切会话
    规则还在、"总是允许"能长期记住。会话层保留作最高优先的一次性覆盖。
    合并只是拼接三 list：deny/ask/allow 的总序由 match_rule 保证（命中即返回），
    来源顺序只影响同一 behavior 内的先后。见 docs/reference/design/runtime/security/permission-decision.html。"""
    from openprogram.runtime.permissions.modes import PermissionRules
    from openprogram.runtime.permissions.session_facts import facts

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
        from openprogram.state.config import read_config
        cfg = read_config()
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
        from openprogram.state.store.project import project_store as _projects
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
        if branch_id:
            from openprogram.runtime.context.session_db import default_db
            from openprogram.state.store.session.branch_config import config_for_branch
            from openprogram.runtime.permissions.modes import as_permission_rules
            row = default_db().get_session(session_id) or {}
            sess = as_permission_rules(config_for_branch(row, branch_id).get("permission_rules"))
        else:
            sess = facts().session_rules(session_id)
    except Exception as exc:
        if branch_id:
            # Losing the owning branch must not silently remove its deny rules.
            raise
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


def rule_origin(session_id: str, raw: str | None, behavior: str) -> str:
    """Which layer holds ``raw`` as a ``behavior`` rule, in the words the settings use.

    The same text can be an allow rule in one layer and an ask rule in another,
    and the refusal must send the owner to the one that decided.
    """
    from openprogram.runtime.permissions.session_facts import facts
    try:
        session = facts().session_rules(session_id)
        if session is not None and raw in getattr(session, behavior):
            return "this conversation"
        from openprogram.state.store.project import project_store as projects
        project = projects.project_for_session(session_id)
        rules = (projects.load_project_settings(project.id).get("permission_rules") or {}) if project else {}
        if raw in (rules.get(behavior) or ()):
            return "this project"
    except Exception as exc:  # noqa: BLE001 - only the wording of a refusal depends on this
        _log.debug("permission rule origin unavailable: %s", exc)
    return "global settings"


def _pattern_spellings(pattern: str) -> list[str]:
    """The pattern as written, plus its native-separator form on Windows.

    A call value there is already spelled with backslashes, and ``fnmatch``
    treats a slash as an ordinary character, so ``**/.env`` copied from a
    Mac would otherwise never meet ``C:\\proj\\.env``.
    """
    if os.name == "nt" and "/" in pattern:
        return [pattern, pattern.replace("/", "\\")]
    return [pattern]


def _match_one(pattern: str, value: str, *, allow: bool) -> bool:
    if pattern.endswith(":*"):
        prefix = pattern[:-2]
        candidate = _effective_shell_command(value, allow=allow)
        if candidate is None:
            return False
        return candidate == prefix or candidate.startswith(prefix + " ")

    def _cmp(pat: str, val: str) -> bool:
        # A host path can mix Git/POSIX slashes with native separators.
        # normpath preserves case, so allow rules remain case-sensitive.
        if os.path.isabs(pat) and os.path.isabs(val):
            pat, val = os.path.normpath(pat), os.path.normpath(val)
        if any(ch in pat for ch in "*?["):
            # fnmatch() applies os.path.normcase() and therefore makes allow
            # rules case-insensitive on Windows. Allows stay exact/case-aware;
            # deny matching performs its own explicit casefold below.
            return fnmatch.fnmatchcase(val, pat)
        return val == pat

    if allow:
        if any(_cmp(pat, value) for pat in _pattern_spellings(pattern)):
            return True
        # The value is already the file's resolved name. Resolve the rule's
        # folder the same way, so ``/tmp/**`` or ``c:\users\me\proj\**``
        # allows what it names; the comparison itself stays case-sensitive.
        if not _is_absolute(pattern):
            return False
        canonical = _realpath_glob_pattern(pattern)
        return canonical != pattern and _cmp(canonical, value)

    seen: set[tuple[str, str]] = set()
    spellings = _pattern_spellings(pattern)
    if os.name == "nt" and re.match(r"\\[^\\/]", pattern):
        # ``\\srv/share/**`` reaches here with one leading backslash: the
        # rule grammar reads a doubled backslash as an escaped one unless
        # plain backslashes follow. Deny and ask also cover the share that
        # was typed, not only a folder of that name on the current drive.
        spellings.append("\\" + pattern)
    patterns = [
        pat for spelling in spellings
        for pat in (spelling, _realpath_glob_pattern(spelling))
    ]
    for pat in patterns:
        for val in _deny_values(value):
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
    value, readings = _split_readings(value)
    if not allow and any(
        pattern_matches(pattern, reading) for reading in readings if reading != _POWERSHELL_UNKNOWN
    ):
        return True
    if value.startswith(_POWERSHELL_UNREAD_PREFIX):
        text = value[len(_POWERSHELL_UNREAD_PREFIX):]
        if allow:
            return pattern == text
        value = shlex.join(_shell_tokens(text)[0])
    if os.name == "nt":
        # The extended-length prefix names the same file; its ``?`` is not
        # a wildcard.
        pattern = _windows_identity(pattern, literal=True)
    parts = [p for p in value.split(_PATH_SEP) if p] if _PATH_SEP in value else [value]
    if allow:
        return all(_match_one(pattern, part, allow=True) for part in parts)
    return any(_match_one(pattern, part, allow=False) for part in parts)

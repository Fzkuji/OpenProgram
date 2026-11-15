"""路径安全判定——acceptEdits 自动放行 / safetyCheck 强制审批的共用底座。

见 docs/reference/design/runtime/security/permission-decision.html。参照 Claude Code 的
filesystem.ts（DANGEROUS_FILES / DANGEROUS_DIRECTORIES + Windows 绕过检测）。

一个路径"安全"的必要条件：
  1. 落在允许的工作目录集内（cwd + additional_working_dirs）；
  2. 不是危险配置文件（.bashrc / .gitconfig / …）；
  3. 不在危险目录内（.git / .openprogram / …）；
  4. 无 Windows 文件名绕过手法（NTFS 流、8.3 短名、UNC、尾部点空格、
     DOS 设备名、三连点遍历）。
任一不满足 → 不安全 → acceptEdits 下不自动放行、safetyCheck 下强制审批。
"""
from __future__ import annotations

import os
import re
import logging

_log = logging.getLogger(__name__)

# 危险配置文件：改它们能改 shell 行为、git 配置、凭据等。
DANGEROUS_FILES = frozenset({
    ".bashrc", ".bash_profile", ".bash_login", ".profile",
    ".zshrc", ".zprofile", ".zshenv",
    ".gitconfig", ".gitmodules", ".git-credentials",
    ".npmrc", ".pypirc", ".netrc",
    ".mcp.json", ".claude.json", ".env",
})

# 危险目录：其内任何写入都不该自动放行。
DANGEROUS_DIRECTORIES = frozenset({
    ".git", ".hg", ".svn",
    ".vscode", ".idea",
    ".openprogram", ".claude", ".ssh", ".gnupg",
})

# A named profile keeps its data beside the default one, as .openprogram-<profile>.
_PROFILE_DIRECTORY_PREFIX = ".openprogram-"

# Windows 文件名绕过：NTFS 数据流、8.3 短名、UNC、尾部点/空格、DOS 设备名、三连点。
_WIN_BYPASS = re.compile(
    r"::\$|"                    # NTFS 备用数据流  foo.txt::$DATA
    r"~\d|"                     # 8.3 短名        PROGRA~1
    r"^\\\\|"                   # UNC 路径        \\server\share
    r"[ .]$|"                   # 尾部点或空格    foo. / foo(空格)
    r"\.\.\.|"                  # 三连点遍历      .../
    r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\.|$)",  # DOS 设备名
    re.IGNORECASE,
)


def _norm_dirs(working_dirs) -> list[str]:
    out = []
    for d in working_dirs or []:
        try:
            out.append(os.path.realpath(d))
        except Exception:
            # An unresolvable working dir is skipped; the cwd is the fallback root.
            _log.debug("working dir %r not resolvable", d, exc_info=True)
    return out or [os.path.realpath(os.getcwd())]


def dangerous_path(path: str, within=(), *, unwatched_read: bool = False) -> str | None:
    """Why ``path`` is off limits (conditions 2 to 4 above), or ``None``.

    Directories are judged below the deepest of ``within`` that holds the
    path, so a project that itself lives under ``.claude`` stays readable.

    ``unwatched_read`` is a read nobody is watching, which has to match what
    the disk opens: letter case counts for nothing, as on macOS and Windows,
    a symlink also answers for the file it points to, and every profile's
    data folder is protected.
    """
    if not path:
        return None
    named = os.path.normpath(path) if unwatched_read else path

    # Windows 绕过（在 realpath 之前查原始字符串——realpath 会规范化掉手法）。
    if _WIN_BYPASS.search(named):
        return f"path uses a Windows filename bypass: {path}"

    ap = os.path.realpath(path)
    fold = str.casefold if unwatched_read else str

    # 危险配置文件（按 basename）。
    names = (named, ap) if unwatched_read else (path,)
    base = next((b for b in map(os.path.basename, names) if fold(b) in DANGEROUS_FILES), None)
    if base:
        return f"dangerous config file: {base}"

    holders = [d for d in map(os.path.realpath, within) if ap == d or ap.startswith(d.rstrip(os.sep) + os.sep)]
    below = ap[len(max(holders, key=len)):] if holders else ap

    # 危险目录（路径任一段命中）。
    def protected(part: str) -> bool:
        part = fold(part)
        return part in DANGEROUS_DIRECTORIES or (unwatched_read and part.startswith(_PROFILE_DIRECTORY_PREFIX))

    hit = next((p for p in below.split(os.sep) if protected(p)), None)
    if hit:
        return f"inside a protected directory: {hit}"
    return None


def check_in_read_refusal(path: str, request) -> str | None:
    """Why a check-in may not read ``path``, as the model named it, or ``None``.

    Other files outside the working directories stay readable.
    """
    if "\0" in path:
        return "the path contains a null character"
    dirs = _turn_dirs(request)
    return dangerous_path(os.path.join(dirs[0], os.path.expanduser(path)), dirs, unwatched_read=True)


def _turn_dirs(request) -> list[str]:
    """The turn's working directories, its first the root a search with no path walks."""
    from openprogram.state.paths import get_default_workdir
    from openprogram.state.worktree.context import current_worktree_path
    return [current_worktree_path() or get_default_workdir(), *getattr(request, "additional_working_dirs", [])]


def read_denier():
    """The host's deny test for a directory walk, plus a check-in's dangerous
    paths, which a walk from a parent directory would otherwise reach."""
    from openprogram.host.sandbox import read_denier as policy_denier
    from openprogram.runtime.agent.turn_request import get_turn_request
    from openprogram.runtime.permissions.identity import check_in

    denied = policy_denier()
    request = get_turn_request()
    if request is None or not check_in(request):
        return denied
    dirs = _turn_dirs(request)
    return lambda target: bool(denied is not None and denied(target)) or dangerous_path(target, dirs, unwatched_read=True) is not None


def check_path_safety(path: str, working_dirs=None) -> dict:
    """返回 {"safe": bool, "message": str}。见模块 docstring 的判定条件。"""
    if not path:
        return {"safe": True, "message": ""}

    reason = dangerous_path(path)
    if reason:
        return {"safe": False, "message": reason}

    ap = os.path.realpath(path)

    # 必须落在工作目录集内。
    dirs = _norm_dirs(working_dirs)
    if not any(ap == d or ap.startswith(d + os.sep) for d in dirs):
        return {"safe": False, "message": f"path is outside the working directory: {ap}"}

    return {"safe": True, "message": ""}

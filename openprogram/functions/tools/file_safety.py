"""路径安全判定——acceptEdits 自动放行 / safetyCheck 强制审批的共用底座。

见 docs/design/runtime/permission-model.md §3.5。参照 Claude Code 的
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

# 命令级危险解释器（危险规则剥离用，§3.5）。
DANGEROUS_BASH_PATTERNS = frozenset({
    "python", "python3", "node", "deno", "bun", "ruby", "perl", "php",
    "sh", "bash", "zsh", "eval", "exec", "source", "sudo", "ssh", "npx",
})

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
            pass
    return out or [os.path.realpath(os.getcwd())]


def check_path_safety(path: str, working_dirs=None) -> dict:
    """返回 {"safe": bool, "message": str}。见模块 docstring 的判定条件。"""
    if not path:
        return {"safe": True, "message": ""}

    base = os.path.basename(path)

    # Windows 绕过（在 realpath 之前查原始字符串——realpath 会规范化掉手法）。
    if _WIN_BYPASS.search(path):
        return {"safe": False, "message": f"path uses a Windows filename bypass: {path}"}

    # 危险配置文件（按 basename）。
    if base in DANGEROUS_FILES:
        return {"safe": False, "message": f"dangerous config file: {base}"}

    ap = os.path.realpath(path)
    parts = ap.split(os.sep)

    # 危险目录（路径任一段命中）。
    hit = next((p for p in parts if p in DANGEROUS_DIRECTORIES), None)
    if hit:
        return {"safe": False, "message": f"inside a protected directory: {hit}"}

    # 必须落在工作目录集内。
    dirs = _norm_dirs(working_dirs)
    if not any(ap == d or ap.startswith(d + os.sep) for d in dirs):
        return {"safe": False, "message": f"path is outside the working directory: {ap}"}

    return {"safe": True, "message": ""}


def is_dangerous_allow_rule(tool_name: str, pattern: str | None) -> bool:
    """危险规则剥离：一条 allow 规则在 acceptEdits 档下会不会放过危险命令。
    `bash(python:*)` 之类命令解释器规则 → 危险（进 auto/acceptEdits 时临时剥离）。"""
    if pattern is None:
        return tool_name.lower() in {"bash", "exec", "shell", "execute_code", "process"}
    head = pattern.split(":", 1)[0].strip().split()[0] if pattern.strip() else ""
    return head.lower() in DANGEROUS_BASH_PATTERNS

"""Permission modes and rule sets: the values every permission decision uses.

Session settings (``conversation.session_config``) store these values and
re-export them; the permission package reads them from here, so it never
imports the conversation package.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# 权限模式规范值（保留驼峰）。见 docs/reference/design/runtime/security/permission-decision.html。
# normalize_permission 做大小写不敏感匹配，所以 "acceptedits" 也能规回 "acceptEdits"。
# 对齐 Claude Code 网页端 Mode 菜单：Ask permissions / Accept edits / Plan mode /
# Auto mode / Bypass permissions。内部值 ask=default档、auto=LLM分类器档。
VALID_PERMISSION = {"ask", "acceptEdits", "plan", "auto", "bypass"}
# The mode a session runs in when neither it nor its project picks one: the
# selector's Bypass. The stored value stays ``bypass``.
# The Web composer shows the same default (use-permission-mode.ts).
DEFAULT_PERMISSION = "bypass"
_PERMISSION_BY_LOWER = {m.lower(): m for m in VALID_PERMISSION}


@dataclass
class PermissionRules:
    """用户在运行时叠加的 allow/deny/ask 规则。三个平行 list，behavior 由
    规则住在哪个 list 决定。规则字符串语法见 openprogram/runtime/permissions/rule.py。
    见 docs/reference/design/runtime/security/permission-decision.html。"""
    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)
    ask: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.allow or self.deny or self.ask)


def normalize_permission(value: Any) -> Optional[str]:
    """The canonical mode for ``value`` (any letter case), or None."""
    if value is None:
        return None
    # 大小写不敏感：驼峰档 acceptEdits 传入任意大小写都规回规范值。
    return _PERMISSION_BY_LOWER.get(str(value).strip().lower())


def resolve_mode(session_value: Any, project_value: Any = None) -> str:
    """The conversation's mode, else its project's, else ``DEFAULT_PERMISSION``.

    An illegal value counts as absent.
    """
    return normalize_permission(session_value) or normalize_permission(project_value) or DEFAULT_PERMISSION


def as_permission_rules(value: Any) -> Optional[PermissionRules]:
    """dict / PermissionRules → PermissionRules；其余 → None。"""
    if isinstance(value, PermissionRules):
        return value
    if isinstance(value, dict):
        return PermissionRules(
            allow=_as_str_list(value.get("allow")),
            deny=_as_str_list(value.get("deny")),
            ask=_as_str_list(value.get("ask")),
        )
    return None


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value if str(v)]
    return []


__all__ = [
    "DEFAULT_PERMISSION", "VALID_PERMISSION", "PermissionRules",
    "as_permission_rules", "normalize_permission", "resolve_mode",
]

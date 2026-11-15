"""The one function that decides whether a well-formed tool call runs.

Design: docs/reference/design/runtime/security/permission-decision.html
"""
from __future__ import annotations

from typing import Any

from openprogram.runtime.permissions import identity, refusal
from openprogram.runtime.permissions.refusal import Decision

# A turn request: the conversation package's ``TurnRequest`` or any object
# with its fields. Permission code reads the fields; it never imports it.
TurnRequest = Any


def _session_poll(name: str, args: dict | None) -> bool:
    """write_stdin that only waits for output or sends Ctrl-C to the agent's own command."""
    if name != "write_stdin" or not isinstance(args, dict):
        return False
    return str(args.get("chars") or "") in {"", "\x03"}


def sends_on_behalf(name: str) -> bool:
    """A tool that puts something in front of other people for the user: an
    MCP tool the adapter marked (send_email, chat.postMessage, create_comment)."""
    from openprogram.programs._host.runtime import get
    return bool(getattr(get(name), "_sends_on_behalf", False))


def _held_outbound(name: str, req) -> bool:
    """The Agent holds what it sends as a draft the user approves."""
    if not sends_on_behalf(name):
        return False
    # A trial runs the draft it was started from, which may not be saved yet.
    snapshot = getattr(req, "profile_snapshot", None)
    if isinstance(snapshot, dict) and snapshot.get("hold_outbound") is True:
        return True
    from openprogram.runtime.permissions.session_facts import facts
    return facts().holds_outbound(getattr(req, "agent_id", None) or "main")


def one_shot_approval(name: str, args: dict | None) -> bool:
    """Calls whose approval covers this request only and never becomes an allow rule.

    Each message an Agent sends for the user is approved alone: approving one
    draft never approves the next.
    """
    return sends_on_behalf(name)


# A screen is granted whole, once: an application, a VM endpoint, or the
# user's own browser, with everything done in it (clicks, typing, page
# scripts, uploads). In the modes that ask, the first touch asks until an
# allow rule (this conversation or the project) names it. Bypass runs it.
# Auto reviews the first call to it and lets that review stand for the
# conversation (``app_grants``). Calls the tool marks approval-free
# (list_apps, the guide, built-in browser Pages) skip all of this.
_APP_GRANT_TOOLS = {"computer_use_agent"}
# Rules written for the tool's earlier name still grant the same app.
_RULE_ALIASES = {"computer_use_agent": {"computer_use"}}
# Audited when one of these runs in a turn with no one at the keyboard.
_DELEGATED_SOURCES = {"agent_spawn", "cron", "scheduler", "mcp"}


def keeps_screen_review(tool_name: str) -> bool:
    """Whether Auto's allowing review of one call stands for the screen it operates."""
    return tool_name in _APP_GRANT_TOOLS


def rules_for_tool(rules, tool_name: str) -> tuple[tuple[str, str], ...]:
    """The rules that can decide a call of ``tool_name``, as (behavior, rule) pairs in order.

    Only these take part in that call's decision (see match_rule), so an
    edit to the rules of other tools leaves the decision as it was.
    """
    if rules is None:
        return ()
    from openprogram.runtime.permissions.rule import parse_rule
    names = {tool_name, *_RULE_ALIASES.get(tool_name, ())}
    return tuple((behavior, raw) for behavior, ruleset in (("deny", rules.deny), ("ask", rules.ask),
                                                          ("allow", rules.allow))
                 for raw in ruleset if parse_rule(raw).tool_name in names)


def match_rule(rules, tool_name: str, args: dict) -> tuple[str | None, str | None]:
    """The owner's rule that decides this call, as (verdict, rule text), or (None, None).

    deny > ask > allow; within each, rules run in the order they were merged.
    """
    if rules is None:
        return None, None
    from openprogram.runtime.permissions.rule import (
        parse_command, parse_rule, pattern_matches, unread_powershell_targets,
    )
    cmd = None  # 惰性求值：只在遇到 per-pattern 规则时解析命令
    # A deny rule for rm, mv or cp meets a PowerShell command it cannot read
    # but that may run one of them: asked about, not refused (as Codex does).
    unread = None
    for behavior, ruleset in (("deny", rules.deny), ("ask", rules.ask), ("allow", rules.allow)):
        if behavior == "ask" and unread is not None:
            return "ask", unread
        for raw in ruleset:
            rv = parse_rule(raw)
            if rv.tool_name != tool_name and rv.tool_name not in _RULE_ALIASES.get(tool_name, ()):
                continue
            if rv.pattern is None:
                return behavior, raw
            if cmd is None:
                cmd = parse_command(tool_name, args)
            if cmd is not None and pattern_matches(
                rv.pattern, cmd, allow=(behavior == "allow"),
            ):
                return behavior, raw
            if behavior == "deny" and unread is None and cmd is not None \
                    and unread_powershell_targets(rv.pattern, cmd):
                unread = raw
    return None, None


def _audit_delegated_risky(tool_name: str, req: "TurnRequest") -> None:
    if tool_name in identity.RISKY_TOOLS and req.source in _DELEGATED_SOURCES:
        from openprogram.runtime.permissions.audit import log_delegated_risky_tool
        log_delegated_risky_tool(
            req.session_id or "", tool_name, req.source, req.authority_tier or "",
        )


def _user_browser(args: dict) -> str:
    """The user's own Chrome or Edge, which the conversation's Page grant does not cover."""
    from openprogram.programs._host import services as _services
    browser = _services.lookup("browser")
    if browser is None:
        return ""
    nested = args.get("args") if isinstance(args.get("args"), dict) else {}
    return browser.browser_of(nested)


def _app_grant_needed(agent_tool, args: dict) -> bool:
    from openprogram.programs import tool_requires_approval
    try:
        needed, _reason = tool_requires_approval(agent_tool, args)
    except Exception:
        return True
    return bool(needed)


def can_approve(req) -> bool:
    """Whether someone is there who may answer an approval card for this turn."""
    from openprogram.state.authority import has_capability, normalize_authority
    from openprogram.runtime.permissions.authority import owner_principal_id

    authority = normalize_authority(req)
    try:
        is_owner = authority.get("principal_id") == owner_principal_id()
    except Exception:
        is_owner = False
    if not (authority and is_owner and authority.get("authority_tier") == "owner"
            and has_capability(authority, "approval.request")):
        return False
    if asks_through_caller(req):
        # A foreground sub-agent: the owner waiting on it in the caller's
        # conversation answers its card (lifecycle.spawn_question_route).
        return True
    return authority.get("speaker_kind") == "owner" and authority.get("interaction") == "interactive"


def asks_through_caller(req) -> bool:
    """Whether this turn's questions are shown in the conversation of the turn that waits on it."""
    route = getattr(req, "asks_through", None)
    return (isinstance(route, dict) and bool(route.get("session_id"))
            and getattr(req, "source", None) == "agent_spawn")


def nobody_answers(req) -> bool:
    """Whether no person is behind this turn to answer a question it asks: a
    scheduled run, a check-in, a sub-agent or follow-up the runtime started, an
    MCP client. A person in a chat app is asked there, whatever their tier."""
    from openprogram.state.authority import normalize_authority

    authority = normalize_authority(req)
    return bool(authority) and authority.get("interaction") != "interactive" and (
        authority.get("speaker_kind") != "human")


def surface_grant_applies(name: str, req, args) -> bool:
    """Whether the conversation's grant over its own browser Pages covers this call."""
    if not (name == "computer_use_agent" and isinstance(args, dict)
            and args.get("target") == "browser" and not _user_browser(args)):
        return False
    from openprogram.runtime.permissions.session_facts import facts
    return facts().browser_pages_available(getattr(req, "surface_context", None))


def _allow(layer: str, code: str, rule: str | None = None) -> Decision:
    return Decision("allow", code, layer, rule=rule)


def _deny(item: refusal.Refusal, rule: str | None = None) -> Decision:
    return Decision("deny", item.code, item.layer, refusal=item, rule=rule)


def _ask_or_nobody(agent_tool, req, args, code: str, layer: str, rule: str | None = None) -> Decision:
    """Ask, where there is someone to ask; otherwise refuse now, so no card is left waiting."""
    if can_approve(req):
        return Decision("ask", code, layer, rule=rule)
    if getattr(req, "run_note", None):
        # A run no one watches (``--print``): refused with the fixed note
        # that tells the model to go on without the call.
        return _deny(refusal.non_interactive(agent_tool.name, args, layer=layer, wanted=code),
                     rule=rule)
    if getattr(req, "source", None) == "agent_spawn":
        # A sub-agent nobody waits on (background, a delivered message):
        # refused now with what to do, never left waiting on a card.
        return _deny(refusal.helper_cannot_ask(agent_tool.name, args, layer=layer, wanted=code,
                                               mode=req.permission_mode, rule=rule), rule=rule)
    from openprogram.runtime.permissions.rule import rule_origin
    return _deny(refusal.no_one_to_approve(
        agent_tool.name, args, source=req.source, mode=req.permission_mode,
        wanted=code, layer=layer, rule=rule,
        origin=rule_origin(req.session_id, rule, "ask") if rule else "global settings",
    ), rule=rule)


def _free_code(agent_tool, req, args) -> str | None:
    """Calls that need no decision in any mode, by the reason they need none."""
    name = agent_tool.name
    if name in _APP_GRANT_TOOLS and req.permission_mode != "auto" and _app_grant_needed(agent_tool, args):
        # Operating a screen asks on first touch in Ask and Plan, whichever target it names.
        return None
    if surface_grant_applies(name, req, args):
        return "SURFACE_GRANT"
    if _session_poll(name, args):
        # The command itself was approved when bash started it.
        return "SESSION_POLL"
    if (name in _APP_GRANT_TOOLS and isinstance(args, dict) and args.get("target") != "browser"
            and not _app_grant_needed(agent_tool, args)):
        # Listing applications or showing a guide touches no application.
        return "APP_GRANT_FREE"
    if req.source in {"cron", "scheduler"} and name in identity.SCHEDULED_MEMORY_TOOLS:
        return "SCHEDULED_MEMORY"
    from openprogram.runtime.permissions.classifier import safe_call
    if safe_call(name, args):
        return "SAFE_TOOL"
    return None


def _mode_decision(agent_tool, req, args) -> Decision:
    name = agent_tool.name
    mode = req.permission_mode
    if mode == "bypass":
        _audit_delegated_risky(name, req)
        return _allow("mode", "MODE_UNRESTRICTED")
    if mode == "plan" and name == "exit_plan_mode":
        return _ask_or_nobody(agent_tool, req, args, "PLAN_EXIT", "mode")
    if name == "exit_plan_mode":
        # In the array in every mode (the array does not follow the mode);
        # outside Plan mode there is no plan to leave.
        return _deny(refusal.not_in_plan(name, args))
    free = _free_code(agent_tool, req, args)
    if free:
        return _allow("mode", free)
    if mode == "acceptEdits" and getattr(agent_tool, "_accept_edits_safe", False) and identity.path_is_safe(name, args, req):
        return _allow("mode", "SAFE_EDIT")
    if mode == "auto":
        if name in _APP_GRANT_TOOLS:
            from openprogram.runtime.permissions import app_grants
            if app_grants.held(req, name, args):
                return _allow("mode", "AUTO_APP_GRANT")
        return Decision("auto", "AUTO_CLASSIFY", "mode")
    return _ask_or_nobody(agent_tool, req, args, "MODE_APPROVAL", "mode")


def permission_decision(agent_tool, req, args: dict) -> Decision:
    """One synchronous decision for both wait publication and execution.

    Auto classification runs only during execution. A malformed call never
    gets here: ``prechecks.call_problem`` sends it back to the model first.
    """
    name = agent_tool.name

    # 1. Identity. Never fires for the owner's own turns; a check-in keeps off
    # dangerous paths, and work from text the owner did not write off scripts and deletes.
    denied = identity.decide(agent_tool, req, args)
    if denied:
        return denied

    # 2. The owner's rules: deny > ask > allow.
    from openprogram.runtime.permissions.rule import rule_origin
    from openprogram.programs import _unsafe_in_for
    verdict, rule = match_rule(getattr(req, "permission_rules", None), name, args)
    if verdict == "deny":
        return _deny(refusal.rule_deny(name, args, rule, rule_origin(req.session_id, rule, "deny")), rule=rule)
    # Plan's read-only promise outranks the owner's ask and allow rules. The
    # tools stay in the array in every mode; the call is what is refused.
    if req.permission_mode == "plan" and "plan" in _unsafe_in_for(name):
        return _deny(refusal.plan(name, args))
    if verdict == "ask":
        return _ask_or_nobody(agent_tool, req, args, "RULE_ASK", "rule", rule)
    if _held_outbound(name, req):
        return _ask_or_nobody(agent_tool, req, args, "RULE_OUTBOUND_HOLD", "rule")
    if verdict == "allow":
        _audit_delegated_risky(name, req)
        return _allow("rule", "RULE_ALLOW", rule)

    # 3. The bottom mode.
    return _mode_decision(agent_tool, req, args)

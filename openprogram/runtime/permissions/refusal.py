"""The one place that says no.

Every refusal the model reads is built here, in one shape::

    [blocked by <layer>] <what> did not run: <why>. To change this: <change>.

``permission_decision`` builds a :class:`Refusal`. Tool code raises
:class:`Refused`, which wraps one; ``approval._gated_execute`` turns it into
the standard denied result. Nothing else builds either class, so a future
gate cannot quietly invent its own wording (``test_refusal_single_source``
keeps it so). A stale-state result is a :class:`Retry`: it tells the model to
try again and is not presented as a block.

Design: docs/reference/design/runtime/security/permission-decision.html
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Layer = Literal["identity", "rule", "mode", "sandbox", "hook", "setting"]


@dataclass(frozen=True)
class Refusal:
    layer: Layer
    code: str
    what: str  # the call that did not run, in plain words
    why: str  # the specific reason; quotes the rule or names the mode
    change: str  # exactly where the owner changes this

    @property
    def text(self) -> str:
        return (f"[blocked by {self.layer}] {self.what} did not run: {self.why.rstrip('. ')}. "
                f"To change this: {self.change.rstrip('. ')}.")


@dataclass(frozen=True)
class Retry:
    """The state a call was checked against moved; the same call may be asked for again."""

    code: str
    text: str


@dataclass(frozen=True)
class Decision:
    """What ``permission_decision`` concluded about one call."""

    outcome: Literal["allow", "ask", "auto", "deny"]
    code: str
    layer: str
    refusal: Refusal | None = None  # set when outcome == "deny"
    rule: str | None = None  # the rule text that decided, if any
    authority: object | None = None  # structured evidence for identity denials


class Refused(Exception):
    """Raised by tool code to refuse a call; built only by this module's constructors."""

    def __init__(self, refusal: Refusal):
        super().__init__(refusal.text)
        self.refusal = refusal

    @property
    def text(self) -> str:
        return self.refusal.text

    def result(self, *, mode: str | None = None):
        return tool_result(self.refusal, mode=mode)


def details(item: Refusal | Retry, *, rule=None, mode=None, authority=None, **extra: Any) -> dict:
    """The ``details`` of a denied tool result."""
    found = {
        "denied": True,
        "reason_code": item.code,
        "layer": getattr(item, "layer", None),
        "rule": rule,
        "mode": mode,
        "outcome": "not_started",
        "execution_started": False,
        **extra,
    }
    if authority is not None:
        found["authority_decision"] = authority.to_dict()
    return found


def tool_result(item: Refusal | Retry, **kwargs):
    from openprogram.models.providers.types import AgentToolResult, TextContent
    return AgentToolResult(content=[TextContent(text=item.text)], details=details(item, **kwargs), is_error=True)


def describe(name: str, args: Any = None) -> str:
    """A tool call in plain words: its name and what it was pointed at."""
    if isinstance(args, dict):
        for key in ("command", "file_path", "path", "url", "action"):
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                target = " ".join(value.split())
                return f'{name} "{target[:77]}..."' if len(target) > 80 else f'{name} "{target}"'
    return name


# -- identity ------------------------------------------------------------------

_IDENTITY_CHANGE = "Channels > account access, or MCP server exposed tools"
_OWNER_MUST_START = "the owner must start this work"


_ROOM_CHANGE = ("if this person has chat access only, the owner can run openprogram channels accounts "
                "group-users add <channel> <user id> --access full; if the account's sender_access is "
                "paired, the owner can run openprogram channels accounts set <channel> sender_access owner")


def authority(name: str, args: Any, decision, speaker: str | None = None, *, room: bool = False) -> Refusal:
    return Refusal("identity", decision.reason_code, describe(name, args),
                   f"{speaker or 'this speaker'} is not the owner and may not {decision.capability}",
                   _ROOM_CHANGE if room else _IDENTITY_CHANGE)


def scope(name: str, args: Any, source: str, action: str) -> Refusal:
    return Refusal("identity", "IDENTITY_SCOPE", describe(name, args),
                   f"work started by {source} may not {action}", _OWNER_MUST_START)


def check_in_read(name: str, args: Any, reason: str) -> Refusal:
    return Refusal("identity", "IDENTITY_CHECK_IN_READ", describe(name, args),
                   f"an always-on check-in may not read this: {reason}",
                   "report what you need and let the owner look in a normal turn")


def page_script(what: str) -> Refused:
    return Refused(_page_script(what))


def page_script_denial(name: str, args: Any) -> Refusal:
    return _page_script(describe(name, args))


def _page_script(what: str) -> Refusal:
    return Refusal("identity", "IDENTITY_PAGE_SCRIPT", what,
                   "page scripts run only in work the owner started",
                   "use the page actions, or have the owner start this")


def outside_text(name: str, args: Any) -> Refusal:
    return Refusal("identity", "IDENTITY_OUTSIDE_TEXT", describe(name, args),
                   "this work came from text the owner did not write (a handoff or an automatic Goal), "
                   "so page scripts and destructive deletes do not run in it",
                   "say what you wanted to do and why in your report or final message, so the owner can do it")


def local_owner_required(name: str, args: Any) -> Refusal:
    return Refusal("identity", "APPROVAL_LOCAL_OWNER_REQUIRED", describe(name, args),
                   "only the owner, at an interactive session, can answer this approval", _OWNER_MUST_START)


def framework_owner(operation: str) -> Refused:
    return Refused(Refusal("identity", "IDENTITY_FRAMEWORK", operation,
                           "framework operations are the owner's", _OWNER_MUST_START))


def framework_backend(operation: str) -> Refused:
    return Refused(Refusal("sandbox", "SANDBOX_FRAMEWORK", operation,
                           "framework operations need the local backend with Sandbox off",
                           "the Sandbox switch for this conversation, or the active backend in settings"))


def framework_permission_settings(operation: str) -> Refused:
    return Refused(Refusal("identity", "IDENTITY_FRAMEWORK_PERMISSIONS", operation,
                           "the permission mode and permission rules are set by the owner, not through a framework command",
                           "the mode selector at the bottom of the message box, the project's settings, "
                           "or Permission rules in Settings"))


def self_update_owner() -> Refused:
    return Refused(Refusal("identity", "IDENTITY_SELF_UPDATE", "self_update",
                           "changing OpenProgram itself is the owner's", _OWNER_MUST_START))


def memory_access(what: str) -> Refused:
    return Refused(Refusal("identity", "MEMORY_ACCESS_DENIED", what,
                           "only the owner reads another conversation", _OWNER_MUST_START))


# -- rules ---------------------------------------------------------------------

def rule_deny(name: str, args: Any, rule: str | None, origin: str) -> Refusal:
    return Refusal("rule", "RULE_DENY", describe(name, args), f'your deny rule "{rule}" matches',
                   f"Permission rules in {origin}")


OUTBOUND_HOLD_SETTING = "Hold what it sends for my approval"
_WANTED = {
    "RULE_OUTBOUND_HOLD": "the Agent holds what it sends for approval",
    "PLAN_EXIT": "leaving Plan mode asks",
    "SANDBOX_ESCALATION": "this command asks to leave the sandbox",
}


def no_one_to_approve(name: str, args: Any, *, source: str, mode: str, wanted: str,
                      layer: Layer, rule: str | None = None, origin: str = "global settings") -> Refusal:
    need = f'rule "{rule}"' if rule else _WANTED.get(wanted, f"{mode} mode")
    return Refusal(layer, "NO_ONE_TO_APPROVE", describe(name, args),
                   f"this needs approval ({need}) and this {source} turn has no one to approve it",
                   _no_approver_change(name, args, wanted, rule, origin))


def helper_cannot_ask(name: str, args: Any, *, layer: Layer, wanted: str, mode: str,
                      rule: str | None = None) -> Refusal:
    """A sub-agent nobody waits on met a call that would ask (permissions/delegation.py)."""
    need = f'rule "{rule}"' if rule else _WANTED.get(wanted, f"{mode} mode")
    return Refusal(layer, "HELPER_CANNOT_ASK", describe(name, args),
                   f"this needs approval ({need}), and a sub-agent running in the background "
                   "has no one to ask",
                   "continue without it and say in your final reply what you needed and why, so the "
                   "agent that started you can do it or ask the person; a sub-agent started in the "
                   "foreground asks in its caller's conversation")


#: What the model reads when a run no one watches (``openprogram --print``)
#: meets a call that would ask: refused at once, and told to go on without it.
NON_INTERACTIVE_TEXT = ("This non-interactive run cannot ask the user for permission, so the "
                        "request was rejected. Continue without this action.")


@dataclass(frozen=True)
class NonInteractive(Refusal):
    """A call refused because the run has no one to ask (``unattended``)."""

    @property
    def text(self) -> str:
        return f"[blocked by {self.layer}] {self.what} did not run. {NON_INTERACTIVE_TEXT}"


def non_interactive(name: str, args: Any, *, layer: Layer, wanted: str) -> Refusal:
    return NonInteractive(layer, "NON_INTERACTIVE", describe(name, args),
                          f"this run has no one to approve it ({wanted})",
                          "continue without this action")


def _no_approver_change(name: str, args: Any, wanted: str, rule: str | None, origin: str) -> str:
    """Only what would work: a hold and an ask rule outrank every allow rule, and leaving the sandbox ignores them."""
    if wanted == "RULE_OUTBOUND_HOLD":
        return ("put the full draft in your final message or report, so the owner can send it; "
                f'to let this Agent send on its own, the owner turns off "{OUTBOUND_HOLD_SETTING}" '
                "at the end of the Agent's Always-on tab")
    if wanted == "RULE_ASK":
        return f'the owner edits or removes the ask rule "{rule}" in Permission rules in {origin}'
    if wanted == "SANDBOX_ESCALATION":
        return "set the mode at the bottom to Bypass permissions, or turn off the Sandbox switch for this conversation"
    return f"set the mode at the bottom to Bypass permissions, or add an allow rule: {_exact_rule(name, args)}"


def _exact_rule(name: str, args: Any) -> str:
    from openprogram.runtime.permissions.rule import exact_rule_for_call, rule_to_string
    try:
        value = exact_rule_for_call(name, args)
    except Exception:  # noqa: BLE001 - the plain tool name is still a valid rule
        value = None
    return rule_to_string(value) if value is not None else name


# -- mode ----------------------------------------------------------------------

_SELECTOR = "the mode selector at the bottom of the message box"


def plan(name: str, args: Any) -> Refusal:
    return Refusal("mode", "PLAN_MODE_DENY", describe(name, args),
                   "the conversation is in Plan mode, which only reads", _SELECTOR)


def not_in_plan(name: str, args: Any) -> Refusal:
    """``exit_plan_mode`` called outside Plan mode: it stays in the tools
    array whatever the mode, so the call is what is refused."""
    return Refusal("mode", "PLAN_MODE_NOT_ACTIVE", describe(name, args),
                   "the conversation is not in Plan mode, so there is no plan to leave",
                   "carry on without it")


def unavailable(name: str, args: Any, why: str) -> Refusal:
    """A tool the conversation's tools array keeps but this turn cannot use
    (``loop_runner.turn_availability``): the call is refused and the model
    carries on another way. Nothing is asked of anyone."""
    return Refusal("setting", "TOOL_UNAVAILABLE", describe(name, args), why,
                   "carry on without it")


def declined(name: str, args: Any, *, layer: Layer = "mode", note: str | None = None) -> Refusal:
    why = f'you declined it and said: "{note}". Follow that instead of retrying this call' if note else "you declined it"
    return Refusal(layer, "APPROVAL_DENIED", describe(name, args), why, "ask again, or change the mode")


def auto(name: str, args: Any, code: str, reason: str) -> Refusal:
    state = "blocked" if code == "AUTO_CLASSIFIER_DENY" else "could not run"
    return Refusal("mode", code, describe(name, args), f"Auto review {state}: {reason}",
                   "the mode selector, or an allow rule")


def code_scope(what: str, why: str) -> Refused:
    return Refused(Refusal("mode", "CODE_CALL_SCOPE", what, why, _SELECTOR))


# -- sandbox -------------------------------------------------------------------

_SANDBOX_CHANGE = "the Sandbox switch for this conversation, or sandbox settings"


def sandbox(what: str, violation: str) -> Refused:
    """A path or address outside the conversation's Sandbox."""
    from openprogram.host.sandbox import resolve_policy
    why = f"Sandbox is on and {violation}" if resolve_policy() is not None else violation
    return Refused(Refusal("sandbox", "SANDBOX_DENIED", what, why, _SANDBOX_CHANGE))


def escalation_declined(name: str, args: Any, note: str | None = None) -> Refusal:
    why = "you did not let this command leave the sandbox"
    return Refusal("sandbox", "SANDBOX_ESCALATION_NOT_APPROVED", describe(name, args),
                   f'{why} and said: "{note}"' if note else why, "ask again, or change the mode")


# -- settings and built-in limits ----------------------------------------------

_BUDGET_SETTING = {
    "generations": ("Max spawn depth", "max_spawn_depth"),
    "messages": ("Max messages per chain", "max_messages"),
    "agents": ("Max spawn fan-out", "max_spawn_fanout"),
    "hops": ("Max message hops", "max_hops"),
    "sends this hour": ("Max messages per agent per hour", "max_sends_per_hour"),
    "dollars": ("Max spend per delegation (USD)", "max_tree_usd"),
}


def agent_budget(what: str, kind: str, limit: int, instead: str) -> Refused:
    """A chain ran into one of the ``agent.*`` budgets; ``instead`` is what to do now."""
    label, key = _BUDGET_SETTING[kind]
    return Refused(Refusal("setting", "AGENT_BUDGET", what,
                           f"this chain has reached {limit} {kind}, so {instead}",
                           f"Settings > Agent > {label} (agent.{key})"))


def outbound_url(what: str, why: str) -> Refused:
    return Refused(Refusal("setting", "OUTBOUND_URL_BLOCKED", what, why,
                           "Settings > Security > Model tool fetches (security.outbound_url.model_tools)"))


def page_access() -> Refusal:
    return Refusal("setting", "PAGE_ACCESS_DISABLED", "the browser target",
                   "this turn's Page reference has agent access off",
                   "send the message again from the App with the Page open")


def computer_use_refused_app(app: str) -> Refused:
    return Refused(Refusal("setting", "COMPUTER_USE_REFUSED_APP", f"computer use of {app}",
                           f"{app} is on the refused list", "Settings > computer_use.refused_apps"))


def computer_use_own_window(mode: str) -> Refused:
    return Refused(Refusal("mode", "COMPUTER_USE_OWN_WINDOW", "computer use of OpenProgram",
                           f"in {mode} the Agent may not operate OpenProgram's own window, where approval cards appear",
                           _SELECTOR))


def computer_use_password_field(what: str) -> Refused:
    return Refused(Refusal("setting", "COMPUTER_USE_PASSWORD_FIELD", what,
                           "typing into password fields is turned off",
                           "Settings > computer_use.refuse_password_fields"))


# -- hooks ---------------------------------------------------------------------

def hook(name: str, args: Any, message: str) -> Refused:
    return Refused(Refusal("hook", "HOOK_DENIED", describe(name, args), f"a hook blocked it: {message}",
                           "Hooks in Settings"))


# -- external MCP clients ------------------------------------------------------
# The MCP server answers its clients in fixed text keyed by decision code; a
# client never reads a rule, a path, or anything else the decision looked at.

MCP_GATE_REFUSALS: dict[str, Refusal] = {
    "IDENTITY_SCOPE": Refusal("identity", "IDENTITY_SCOPE", "this call",
                              "work started by an MCP client may not do this", _OWNER_MUST_START),
    "RULE_DENY": Refusal("rule", "RULE_DENY", "this call", "a deny rule matches", "Permission rules"),
    "NO_ONE_TO_APPROVE": Refusal("mode", "NO_ONE_TO_APPROVE", "this call",
                                 "this needs approval and an MCP client has no one to approve it",
                                 "the owner allows this tool in Permission rules, "
                                 "with no ask rule or outbound hold on it"),
}


def mcp_authority(name: str, decision) -> Refusal:
    return authority(name, None, decision, "MCP client")


# -- stale state ---------------------------------------------------------------

def stale(code: str, instruction: str) -> Retry:
    """The call was checked against state that has moved; tell the model to ask again."""
    return Retry(code, instruction)


__all__ = ["Decision", "MCP_GATE_REFUSALS", "NON_INTERACTIVE_TEXT", "NonInteractive", "Refusal",
           "Refused", "Retry", "details", "non_interactive", "tool_result"]

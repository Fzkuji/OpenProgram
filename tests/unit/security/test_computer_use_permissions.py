"""computer_use under the permission rules: every decision asks the target's provider.

The rules, the modes, the authority table, page scripts and the operating
system's grants read a call only through its provider (``tool.rule_key``,
``tool.approval``, ``tool.authority``, ``tool.page_surface``,
``tool.evaluates``, ``tool.system_access``). The providers here are the
stand-ins of ``tests/support/screen_targets.py``.

Design: docs/reference/design/integrations/computer-use/machines-and-targets.html
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from openprogram.programs.tools.computer_use.computer_use import tool as entry
from openprogram.runtime.permissions.rule import exact_rule_for_call, parse_command, parse_rule, rule_to_string
from tests.support import screen_targets

NAME = "computer_use"


@pytest.fixture(autouse=True)
def targets(monkeypatch):
    return screen_targets.install(monkeypatch, *screen_targets.every_kind())


def _key(call):
    return parse_command(NAME, call)


# ------------------------------------------------------------------ rule keys

@pytest.mark.parametrize("call,key", [
    ({"type": "window", "app": "  Calculator ", "action": "click", "args": {"element": 12}}, "window calculator"),
    ({"type": "window", "app": "Google Chrome"}, "window google chrome"),
    ({"type": "window", "code": "pass", "app": "Notes"}, "window notes"),
    # The application inside args is read the same.
    ({"type": "window", "action": "click", "args": {"app": "Calculator", "element": 12}}, "window calculator"),
    ({"type": "screen", "action": "click", "args": {"x": 1, "y": 2}}, "screen"),
    ({"type": "browser", "app": "chrome", "action": "evaluate", "args": {"script": "1"}}, "browser chrome"),
    ({"type": "browser", "app": "edge", "action": "list_tabs"}, "browser edge"),
    ({"device": "lab", "type": "screen", "action": "click", "args": {"x": 1, "y": 2}}, "lab screen"),
    # Types are matched without regard to case (devices: see the registry tests).
    ({"device": "lab", "type": "Screen", "action": "get_state"}, "lab screen"),
])
def test_each_target_is_remembered_under_its_own_key(call, key):
    assert _key(call) == key
    rule = rule_to_string(exact_rule_for_call(NAME, call))
    assert rule == f"{NAME}({key})"
    assert parse_rule(rule).pattern == key


@pytest.mark.parametrize("call", [
    {"type": "window", "action": "list_apps"},
    {"type": "window", "action": "guide"},
    {"type": "window", "action": "click"},  # no application named: asked each time
    {"type": "browser", "action": "click", "args": {"ref": "e1"}},
    {"type": "browser", "action": "upload_file", "args": {"path": "/tmp/a.txt"}},
    {"type": "screen", "action": "list_displays"},
    {"type": "printer", "action": "click"},
    {"type": "window", "action": "fly", "args": {"app": "Notes"}},
])
def test_a_call_that_names_nothing_to_remember_has_no_rule(call):
    assert _key(call) is None
    assert exact_rule_for_call(NAME, call) is None


def test_the_sentence_shown_comes_from_the_provider():
    assert entry.requires_approval(type="window", action="list_apps") is False
    assert entry.requires_approval(type="window", action="click", args={"app": "Calculator"}) \
        == "computer_use controls calculator"
    assert entry.requires_approval(type="window", action="get_state") == "computer_use controls an application"
    for call in ({}, {"code": "pass"}):
        assert entry.requires_approval(type="window", args={"app": "Notes"}, **call) == "computer_use controls notes"
    assert entry.requires_approval(type="screen", action="click", args={"x": 1, "y": 1}) \
        == "computer_use controls the whole screen of this computer"
    assert entry.requires_approval(type="browser", app="chrome", action="list_tabs") == "computer_use controls your Google Chrome"
    assert entry.requires_approval(type="browser", app="edge") == "computer_use controls your Microsoft Edge"
    # A machine that was set up asks nothing; neither do the conversation's own Pages.
    assert entry.requires_approval(device="lab", type="screen", action="click", args={"x": 1, "y": 1}) is False
    assert entry.requires_approval(type="browser", action="click", args={"ref": "e1"}) is False
    assert "Upload" in entry.requires_approval(type="browser", action="upload_file", args={"path": "/tmp/a"})


def test_the_rule_grammar_reads_every_key():
    for key in ("window calculator", "window:*", "window google chrome", "screen", "browser chrome", "browser edge",
                "browser:*", "lab screen", "lab:*", "* screen"):
        rule = f"{NAME}({key})"
        assert parse_rule(rule).pattern == key
        assert rule_to_string(parse_rule(rule)) == rule
    # An application name with parentheses survives the round trip.
    odd = exact_rule_for_call(NAME, {"type": "window", "action": "click", "args": {"app": "Notes (Beta)"}})
    assert parse_rule(rule_to_string(odd)) == odd


# ------------------------------------------------------------------ the decision

@pytest.fixture
def decide(monkeypatch):
    """permission_decision for computer_use, with authority and recovery stubbed out."""
    from openprogram.programs._host.runtime import get
    from openprogram.runtime.permissions import identity, policy, tool_authority
    from openprogram.runtime.permissions.auto_mode import app_grants

    monkeypatch.setattr(identity, "scope_violation", lambda *a, **k: None)
    monkeypatch.setattr(policy, "can_approve", lambda req: req.source == "web")
    monkeypatch.setattr(tool_authority, "decide_tool_authority",
                        lambda *a, **k: SimpleNamespace(allowed=True, reason_code="", capability=""))
    agent_tool = get(NAME)
    app_grants.forget()

    def request(mode="auto", *, allow=(), ask=(), deny=(), source="web", tier="owner", session="s-test"):
        rules = SimpleNamespace(allow=list(allow), ask=list(ask), deny=list(deny))
        return SimpleNamespace(permission_mode=mode, permission_rules=rules, session_id=session, source=source,
                               surface_context=None, authority_tier=tier, principal_id="owner")

    def run(call, mode="auto", **kwargs):
        decision = policy.permission_decision(agent_tool, request(mode, **kwargs), call)
        return decision.outcome, decision.code

    run.request = request
    yield run
    app_grants.forget()


@pytest.fixture
def pages(monkeypatch):
    """The conversation has Pages open."""
    from openprogram.runtime.permissions import session_facts
    monkeypatch.setattr(type(session_facts.facts()), "browser_pages_available", lambda self, context: True)


CLICK_CALCULATOR = {"type": "window", "action": "click", "args": {"app": "Calculator", "element": 3}}
CLICK_SCREEN = {"type": "screen", "action": "click", "args": {"x": 10, "y": 10}}
CLICK_CHROME = {"type": "browser", "app": "chrome", "action": "click", "args": {"tab": "t1", "ref": "e1"}}
CLICK_LAB = {"device": "lab", "type": "screen", "action": "click", "args": {"x": 10, "y": 10}}
CLICK_PAGE = {"type": "browser", "action": "click", "args": {"ref": "e1"}}


@pytest.mark.parametrize("mode", ["default", "acceptEdits"])
@pytest.mark.parametrize("call", [CLICK_CALCULATOR, CLICK_SCREEN, CLICK_CHROME])
def test_the_first_touch_of_a_screen_asks_in_the_modes_that_ask(decide, mode, call):
    assert decide(call, mode) == ("ask", "MODE_APPROVAL")


def test_bypass_runs_and_auto_reviews_without_a_grant(decide):
    for call in (CLICK_CALCULATOR, CLICK_SCREEN, CLICK_CHROME):
        assert decide(call, "bypass") == ("allow", "MODE_UNRESTRICTED")
        assert decide(call, "auto") == ("auto", "AUTO_CLASSIFY")


def test_an_allow_rule_names_one_screen(decide):
    allow = ["computer_use(window calculator)"]
    assert decide(CLICK_CALCULATOR, "default", allow=allow) == ("allow", "RULE_ALLOW")
    notes = {"type": "window", "action": "click", "args": {"app": "Notes"}}
    assert decide(notes, "default", allow=allow) == ("ask", "MODE_APPROVAL")
    assert decide(CLICK_SCREEN, "default", allow=allow) == ("ask", "MODE_APPROVAL")
    # A prefix covers every application and nothing else.
    every_app = ["computer_use(window:*)"]
    assert decide(notes, "default", allow=every_app) == ("allow", "RULE_ALLOW")
    assert decide(CLICK_SCREEN, "default", allow=every_app) == ("ask", "MODE_APPROVAL")
    assert decide(CLICK_SCREEN, "default", allow=["computer_use(screen)"]) == ("allow", "RULE_ALLOW")
    assert decide(CLICK_CHROME, "default", allow=["computer_use(browser chrome)"]) == ("allow", "RULE_ALLOW")
    edge = {**CLICK_CHROME, "type": "browser", "app": "edge"}
    assert decide(edge, "default", allow=["computer_use(browser chrome)"]) == ("ask", "MODE_APPROVAL")
    # The conversation's own pages are not the user's browser.
    assert decide(CLICK_PAGE, "default", allow=["computer_use(browser chrome)"]) == ("ask", "MODE_APPROVAL")


def test_a_rule_under_the_old_keys_or_the_old_tool_name_grants_nothing(decide):
    for rule in ("computer_use(calculator)", "computer_use(app calculator)", "computer_use(app:*)",
                 "computer_use(chrome)", "computer_use(playwright_in_chrome)", "computer_use(browser:chrome)"):
        assert decide(CLICK_CALCULATOR, "default", allow=[rule]) == ("ask", "MODE_APPROVAL")
        assert decide(CLICK_CHROME, "default", allow=[rule]) == ("ask", "MODE_APPROVAL")


def test_a_machine_that_was_set_up_is_operated_without_asking(decide):
    for mode in ("default", "acceptEdits", "auto"):
        assert decide(CLICK_LAB, mode) == ("allow", "APP_GRANT_FREE")
        assert decide({"device": "lab", "type": "screen"}, mode) == ("allow", "APP_GRANT_FREE")
    assert decide(CLICK_LAB, "bypass") == ("allow", "MODE_UNRESTRICTED")


@pytest.mark.parametrize("rule", [
    "computer_use(lab screen)",
    "computer_use(lab:*)",
    "computer_use(* screen)",
    "computer_use(Lab Screen)",
    "computer_use",
])
def test_the_owners_deny_rule_reaches_a_machine(decide, rule):
    for mode in ("default", "auto", "bypass"):
        assert decide(CLICK_LAB, mode, deny=[rule]) == ("deny", "RULE_DENY")
    assert decide(CLICK_LAB, "default", ask=[rule]) == ("ask", "RULE_ASK")


def test_a_deny_rule_for_one_machine_leaves_the_others(decide, targets, monkeypatch):
    screen_targets.install(monkeypatch, *targets.values(), screen_targets.MachineScreen("lab-2"))
    deny = ["computer_use(lab:*)"]
    assert decide(CLICK_LAB, "default", deny=deny) == ("deny", "RULE_DENY")
    assert decide({**CLICK_LAB, "device": "lab-2", "type": "screen"}, "default", deny=deny) == ("allow", "APP_GRANT_FREE")
    # Nor does a machine's rule reach this computer.
    assert decide(CLICK_SCREEN, "default", deny=["computer_use(lab:*)"]) == ("ask", "MODE_APPROVAL")
    assert decide(CLICK_SCREEN, "default", deny=["computer_use(* screen)"]) == ("ask", "MODE_APPROVAL")


@pytest.mark.parametrize("app", ["Grandma's Notes", "Notes & Co", 'Say "hi"'])
def test_a_deny_prefix_reads_an_application_name_shells_cannot_parse(decide, app):
    call = {"type": "window", "action": "click", "args": {"app": app}}
    for rule in ("computer_use(window:*)", f"computer_use(window {app.split()[0].lower()}:*)"):
        assert decide(call, "bypass", deny=[rule]) == ("deny", "RULE_DENY")
    assert decide(call, "bypass", deny=["computer_use(screen:*)"]) == ("allow", "MODE_UNRESTRICTED")


@pytest.mark.parametrize("app", ["[a-z]*", "notes:*", "no?es", "*"])
def test_an_application_name_the_matcher_reads_as_a_wildcard_is_allowed_once_only(app):
    call = {"type": "window", "action": "click", "args": {"app": app}}
    assert _key(call) is not None
    assert exact_rule_for_call(NAME, call) is None


def test_a_target_that_does_not_resolve_is_never_free(decide, monkeypatch):
    from openprogram.programs._authoring.prechecks import call_problem
    from openprogram.programs.tools.computer_use.computer_use import registry
    unknown = {"type": "printer", "action": "click"}
    # The model hears of it before any decision: no card, no review, no refusal for nobody to approve.
    problem = call_problem(NAME, unknown)
    assert problem.code == "INVALID_ARGUMENTS"
    assert "UNKNOWN_TARGET: type is one of window, screen, browser, not 'printer'. Targets now: " in problem.text
    assert 'type="window"' in problem.text
    assert call_problem(NAME, {"type": "window", "action": "list_apps"}) is None
    # A call that passed the check but no longer resolves at the decision asks.
    assert decide(unknown, "default") == ("ask", "MODE_APPROVAL")
    assert decide(unknown, "auto") == ("auto", "AUTO_CLASSIFY")
    listing = {"type": "window", "action": "list_apps"}
    assert decide(listing, "default") == ("allow", "APP_GRANT_FREE")

    def fails(*_a, **_k):
        raise RuntimeError("a kind failed to open")

    # A target list that fails to read at decision time asks; dispatch may read it whole.
    monkeypatch.setattr(registry, "find", fails)
    assert decide(listing, "default") == ("ask", "MODE_APPROVAL")


def test_listing_and_the_guide_need_no_grant(decide):
    for mode in ("default", "acceptEdits", "auto"):
        for call in ({"type": "window", "action": "list_apps"}, {"type": "window", "action": "guide"},
                     {"type": "screen", "action": "list_displays"}):
            assert decide(call, mode) == ("allow", "APP_GRANT_FREE")
    assert decide(CLICK_CALCULATOR, "default", source="cron")[0] == "deny"


def test_auto_reviews_a_screen_once_per_conversation_under_its_key(decide):
    from openprogram.runtime.permissions.auto_mode import app_grants
    assert decide(CLICK_CALCULATOR) == ("auto", "AUTO_CLASSIFY")
    app_grants.grant(decide.request(), NAME, CLICK_CALCULATOR)
    assert decide(CLICK_CALCULATOR) == ("allow", "AUTO_APP_GRANT")
    assert decide({"type": "window", "code": "pass", "args": {"app": "calculator"}}) == ("allow", "AUTO_APP_GRANT")
    for other in ({"type": "window", "action": "click", "args": {"app": "Notes"}}, CLICK_SCREEN, CLICK_CHROME):
        assert decide(other) == ("auto", "AUTO_CLASSIFY")
    assert decide(CLICK_CALCULATOR, session="s-other") == ("auto", "AUTO_CLASSIFY")
    assert decide(CLICK_CALCULATOR, "default") == ("ask", "MODE_APPROVAL")
    # A rule naming the tool appeared: the review is made again.
    assert decide(CLICK_CALCULATOR, allow=["computer_use(window notes)"]) == ("auto", "AUTO_CLASSIFY")
    app_grants.grant(decide.request(), NAME, CLICK_SCREEN)
    assert decide(CLICK_SCREEN) == ("allow", "AUTO_APP_GRANT")
    app_grants.grant(decide.request(), NAME, CLICK_CALCULATOR)
    # A rule for another tool changes nothing, and with the rules as they were the review still stands.
    assert decide(CLICK_CALCULATOR, allow=["bash(ls:*)"]) == ("allow", "AUTO_APP_GRANT")
    assert decide(CLICK_CALCULATOR) == ("allow", "AUTO_APP_GRANT")
    # Forgetting the conversation ends what auto kept.
    app_grants.forget("s-test")
    assert decide(CLICK_CALCULATOR) == ("auto", "AUTO_CLASSIFY")


def test_an_allowing_auto_review_of_a_screen_is_kept_and_a_refusal_is_not(decide, monkeypatch):
    import asyncio
    from openprogram.programs._host.runtime import get
    from openprogram.runtime.permissions.auto_mode import auto_history, auto_review, classifier
    from openprogram.runtime.permissions.auto_mode.classifier import ReviewResult

    verdicts = iter([ReviewResult(True, "no", "AUTO_CLASSIFIER_DENY"), ReviewResult(False, "ok", "AUTO_CLASSIFIER_ALLOW")])
    reviewed = []

    async def review(name, args, **_):
        reviewed.append(args["args"]["app"])
        return next(verdicts)

    monkeypatch.setattr(classifier, "auto_classify_tool", review)
    monkeypatch.setattr(auto_history, "for_operation", lambda *a, **k: None)
    request = decide.request()
    request.additional_working_dirs, request.user_text = [], "add these up"
    # Keeps the review from re-reading live session settings, which this test has none of.
    request.interaction = "non-interactive"
    for expected in (("auto", "AUTO_CLASSIFY"), ("allow", "AUTO_APP_GRANT")):
        prepared = asyncio.run(auto_review.prepare_review(get(NAME), request, "call", CLICK_CALCULATOR))
        assert prepared is not None
        assert decide(CLICK_CALCULATOR) == expected
    # The third call is not sent to the reviewer at all.
    asyncio.run(auto_review.prepare_review(get(NAME), request, "call-3", CLICK_CALCULATOR))
    assert reviewed == ["Calculator", "Calculator"]


def test_the_approval_card_offers_the_conversation_scope():
    from openprogram.runtime.execution.waits import _APPROVAL_SCOPES
    from openprogram.runtime.permissions.approvals import approval
    assert NAME in approval._SESSION_SCOPE_TOOLS
    assert "session" in _APPROVAL_SCOPES


# ------------------------------------------------------------------ Pages, the user's browser, page scripts

def test_the_conversations_pages_have_their_own_grant(decide, pages):
    for mode in ("default", "acceptEdits", "auto"):
        assert decide(CLICK_PAGE, mode) == ("allow", "SURFACE_GRANT")
        assert decide({"type": "browser", "code": "pass"}, mode) == ("allow", "SURFACE_GRANT")
    # An upload is approved alone where the modes ask.
    upload = {"type": "browser", "action": "upload_file", "args": {"path": "/tmp/a.txt"}}
    assert decide(upload, "default") == ("ask", "MODE_APPROVAL")
    # In Auto the Pages grant does not cover it either: it is reviewed each time.
    assert decide(upload, "auto") == ("auto", "AUTO_CLASSIFY")
    from openprogram.runtime.permissions.policy import surface_grant_applies
    assert not surface_grant_applies(NAME, decide.request("auto"), upload)


def test_without_pages_the_built_in_browser_asks_as_before(decide):
    assert decide(CLICK_PAGE, "default") == ("ask", "MODE_APPROVAL")
    assert decide(CLICK_PAGE, "auto") == ("auto", "AUTO_CLASSIFY")


def test_the_users_own_browser_never_falls_under_the_pages_grant(decide, pages):
    from openprogram.runtime.permissions.policy import surface_grant_applies
    for app in ("chrome", "edge"):
        call = {**CLICK_CHROME, "app": app}
        assert not entry.page_surface(call)
        assert not surface_grant_applies(NAME, decide.request("default"), call)
        assert decide(call, "default") == ("ask", "MODE_APPROVAL")
        assert decide(call, "auto") == ("auto", "AUTO_CLASSIFY")
    assert surface_grant_applies(NAME, decide.request("default"), CLICK_PAGE)


def test_the_capability_comes_from_the_provider():
    from openprogram.runtime.permissions.tool_authority import capability_for_tool
    assert capability_for_tool(NAME, CLICK_PAGE) == "browser.control"
    assert capability_for_tool(NAME, {"type": "browser"}) == "browser.control"
    for call in ({"type": "browser", "code": "pass"}, CLICK_CHROME, {**CLICK_CHROME, "type": "browser", "app": "edge"},
                 CLICK_CALCULATOR, CLICK_SCREEN, CLICK_LAB, {"type": "printer"}):
        assert capability_for_tool(NAME, call) == "process.exec"
    assert capability_for_tool(NAME) == "process.exec"


def test_a_page_script_needs_what_any_other_action_in_that_browser_needs(decide, monkeypatch):
    from openprogram.runtime.permissions import identity, policy, session_facts
    script = {"type": "browser", "actions": [{"action": "evaluate", "args": {"script": "1"}}]}
    assert identity.page_script(NAME, script)
    assert not policy.one_shot_approval(NAME, script)
    assert not identity.page_script(NAME, CLICK_PAGE)
    # Code is checked step by step inside the interpreter.
    assert not identity.page_script(NAME, {"type": "browser", "code": "await cu.evaluate(script='1')"})
    assert exact_rule_for_call(NAME, script) is None
    for mode in ("default", "acceptEdits", "auto", "bypass"):
        assert decide(script, mode) == decide(CLICK_PAGE, mode)
    monkeypatch.setattr(type(session_facts.facts()), "browser_pages_available", lambda self, context: True)
    for mode in ("default", "acceptEdits", "auto"):
        assert decide(script, mode) == ("allow", "SURFACE_GRANT")
    for source in ("cron", "scheduler"):
        assert decide(script, "bypass", source=source) == ("allow", "MODE_UNRESTRICTED")
    assert decide(script, "bypass", source="mcp") == ("deny", "IDENTITY_PAGE_SCRIPT")
    assert decide(script, "bypass", source="agent_spawn", tier="guest") == ("deny", "IDENTITY_PAGE_SCRIPT")
    assert decide(script, "bypass", source="agent_spawn") == ("allow", "MODE_UNRESTRICTED")
    # In the user's Chrome the grant to operate it covers scripts in it.
    chrome = {"type": "browser", "app": "chrome", "action": "evaluate", "args": {"script": "1"}}
    assert identity.page_script(NAME, chrome)
    assert decide(chrome, "default") == ("ask", "MODE_APPROVAL")
    assert decide(chrome, "default", allow=["computer_use(browser chrome)"]) == ("allow", "RULE_ALLOW")
    assert decide(chrome, "bypass", source="mcp") == ("deny", "IDENTITY_PAGE_SCRIPT")


def test_the_pages_probe_is_whichever_target_the_providers_say(monkeypatch):
    assert entry.page_surface_call() == {"type": "browser"}
    screen_targets.install(monkeypatch, screen_targets.App(), screen_targets.UserBrowser())
    assert entry.page_surface_call() is None


# ------------------------------------------------------------------ operating-system access

def test_system_access_is_what_the_provider_needs():
    import openprogram.runtime  # noqa: F401 - fills the host's port
    from openprogram.host.system_access import _desktop_operation
    assert _desktop_operation(NAME, {"type": "window", "action": "list_apps"}) is None
    assert _desktop_operation(NAME, {"type": "window"}) is None
    assert _desktop_operation(NAME, CLICK_CALCULATOR) == "computer_use:desktop"
    assert _desktop_operation(NAME, {"type": "window", "code": "pass"}) == "computer_use:desktop"
    assert _desktop_operation(NAME, CLICK_SCREEN) == "computer_use:desktop"
    assert _desktop_operation(NAME, {"type": "screen", "action": "list_displays"}) is None
    for call in (CLICK_PAGE, CLICK_CHROME, CLICK_LAB, {"type": "printer"},
                 {"type": "window", "action": "guide", "args": {"app": "Notes"}}):
        assert _desktop_operation(NAME, call) is None
    assert _desktop_operation("bash", {"command": "ls"}) is None


def test_a_missing_grant_is_said_with_its_code_and_where_to_grant_it(monkeypatch):
    import sys
    import openprogram.runtime  # noqa: F401
    from openprogram.host import system_access
    monkeypatch.setattr(system_access.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(system_access, "_native_probe", lambda: {
        "identity": {"executable": sys.executable}, "capabilities": {
            "screen_recording": {"status": "granted", "detail": "fresh"},
            "accessibility": {"status": "not_granted", "detail": "fresh"}}})
    text = system_access.missing_access_error(NAME, CLICK_SCREEN)
    assert text.startswith("PERMISSION_MISSING: macOS has not granted OpenProgram Desktop control.")
    assert "Privacy & Security > Accessibility" in text
    assert system_access.missing_access_error(NAME, CLICK_LAB) is None


def test_recovery_knows_the_screen_tool_by_its_one_name():
    from openprogram.runtime.permissions import recovery
    assert recovery.SCREEN_TOOLS == {NAME}

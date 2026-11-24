"""Coded failures as the model reads them, and what a call tells its provider."""
from __future__ import annotations

import time

import pytest

from openprogram.programs.tools.computer_use.computer_use import errors
from openprogram.programs.tools.computer_use.computer_use.contract import (
    Approval, Call, Described, Provider, Result, refused,
)

# The design's error table, in its order.
DESIGN_CODES = ("UNKNOWN_TARGET", "UNKNOWN_ACTION", "NOT_SUPPORTED", "BAD_ARGUMENT", "PERMISSION_MISSING",
                "DEPENDENCY_MISSING", "MACHINE_UNREACHABLE", "OUTCOME_UNKNOWN", "SCREEN_BUSY", "ELEMENT_GONE",
                "TIMEOUT")

ERRORS = (errors.UnknownTarget, errors.UnknownAction, errors.NotSupported, errors.BadArgument,
          errors.PermissionMissing, errors.DependencyMissing, errors.MachineUnreachable, errors.OutcomeUnknown,
          errors.ScreenBusy, errors.ElementGone, errors.Timeout)

# Only this may have reached the target as such: a step was sent and no answer came. A timeout says so only
# when the provider that stopped had sent steps.
SENT = {errors.OutcomeUnknown}


def test_the_codes_are_the_designs():
    assert errors.CODES == DESIGN_CODES


@pytest.mark.parametrize("error", ERRORS)
def test_a_coded_answer_is_code_colon_message(error):
    assert error("the reason").text() == f"{error.code}: the reason"
    assert refused(error("the reason")).text == f"{error.code}: the reason"
    assert refused(error("the reason")).is_error


@pytest.mark.parametrize("error", ERRORS)
def test_action_dispatched_is_false_exactly_when_nothing_was_sent(error):
    data = refused(error("x")).json_data
    if error in SENT:
        assert error.sent and data is None
    else:
        assert not error.sent and data == {"action_dispatched": False}


def test_call_check_passes_before_the_deadline_and_says_timeout_after():
    Call("c", "a", deadline=time.monotonic() + 60).check()
    Call("c", "a").check()
    with pytest.raises(errors.Timeout) as caught:
        Call("c", "a", deadline=time.monotonic() - 1).check()
    assert caught.value.text().startswith("TIMEOUT: ")


def test_a_timeout_after_steps_took_effect_is_marked_sent_by_its_provider():
    with pytest.raises(errors.Timeout) as caught:
        Call("c", "a", deadline=time.monotonic() - 1).check()
    # The deadline is checked before the next step goes out: nothing was sent.
    assert refused(caught.value).json_data == {"action_dispatched": False}
    stopped = errors.Timeout("the call ran out of time; steps that took effect: click")
    stopped.sent = True
    assert refused(stopped).json_data is None


def test_call_check_raises_what_cancellation_raises():
    class Stopped(Exception):
        pass

    def stop():
        raise Stopped()

    call = Call("c", "a", check_cancelled=stop)
    with pytest.raises(Stopped):
        call.check()
    assert call.cancelled()
    # A deadline that passed is a timeout, not a cancellation.
    assert not Call("c", "a", deadline=time.monotonic() - 1).cancelled()
    assert not Call("c", "a").cancelled()


def test_a_call_reads_images_unless_told_otherwise():
    assert Call("c", "a").images and not Call("c", "a", images=False).images


def test_a_result_becomes_a_tool_return():
    returned = Result("Done", images=[b"png"], data={"tab": "t1"}, failed=True).tool_return()
    assert (returned.text, returned.images, returned.json_data, returned.is_error) == \
        ("Done", [b"png"], {"tab": "t1"}, True)


def test_an_approval_without_a_question_asks_nothing_and_keeps_its_rule():
    asked = Approval(None, "lab screen")
    assert asked.shown is None and asked.rule == "lab screen"


def test_a_providers_defaults_ask_nothing_and_send_nothing():
    class Bare(Provider):
        family, machine = "screen", "x"

        def describe(self):
            return Described("bare")

    bare = Bare()
    assert bare.machine == "x" and bare.state_scope == "agent"
    assert bare.opening({}) == [("get_state", {})]
    assert bare.approval({}, []) is None and not bare.lock({}, [])
    assert bare.authority([], False) == "process.exec" and bare.system_access({}, [], False) is None
    assert not bare.runs_page_script([("evaluate", {})])
    assert bare.resource({}) is None and bare.created("c") == [] and bare.claims({}, "c") == ""
    assert bare.environment_note("devbox", {"devbox screen"}) == ""
    with pytest.raises(errors.NotSupported):
        bare.list("tabs", {}, {}, Call("c", "a"))
    surface = bare.surface()
    assert (surface.target, surface.family) == ('device="x", type="screen"', "screen")


@pytest.mark.parametrize("machine, family, app, target, address", [
    ("local", "window", "", "window", 'type="window"'),
    ("local", "screen", "", "screen", 'type="screen"'),
    ("local", "browser", "", "browser", 'type="browser"'),
    ("local", "browser", "chrome", "browser chrome", 'type="browser", app="chrome"'),
    ("local", "browser", "edge", "browser edge", 'type="browser", app="edge"'),
    ("lab", "screen", "", "lab screen", 'device="lab", type="screen"'),
])
def test_a_targets_key_and_address_come_from_its_device_type_and_app(machine, family, app, target, address):
    class Named(Provider):
        def describe(self):
            return Described("named")

    named = Named()
    named.machine, named.family, named.app = machine, family, app
    assert named.target == target and named.address() == address
    assert named.device == ("" if machine == "local" else machine)
    assert named.addressed() == {key: value for key, value in
                                 (("device", named.device), ("type", family), ("app", app)) if value}

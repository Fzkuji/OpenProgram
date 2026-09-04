"""Actual UI/native programs cross the controlled HTTP/WS backend and Job receipt."""
import base64
import json
from pathlib import Path
import subprocess

import pytest

from tests.component.self_update.test_ui_checks import (  # noqa: F401
    _isolated_owner, _test_object_plan, _public_prepare, consume, http_live, installed_cli,
    live, package_factory, store_fixture, ui_install, verifier,
)

ROOT = Path(__file__).resolve().parents[3]


def _native_driver(control):
    def capture(url, token, nonce, png):
        from openprogram.self_update import ui_checks
        with ui_checks._lock:
            contract = dict(ui_checks._pending[nonce]["contract"])
        state = control["state"]
        cfg = dict(contract=contract, url=url, token=token, png=base64.b64encode(png).decode(),
                   command_url=f"http://127.0.0.1:{state.port}/api/test-ui-fixture", interrupt=control.get("interrupt", False))
        result = subprocess.run(["node", str(ROOT / "apps/web/tests/self-update-test-object.test.mjs"), "--backend"],
                                cwd=ROOT, input=json.dumps(cfg), capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stderr
        control["native_result"] = json.loads(result.stdout)
        return control["native_result"].get("ok") is True
    return capture


@pytest.mark.parametrize("verifier", [_test_object_plan()], indirect=True)
def test_real_rename_control_backend_restoration_and_signed_result(verifier, ui_install):
    from openprogram.self_update import ui_checks
    ui_install["native_capture"] = _native_driver(ui_install)
    verifier.run()
    result = verifier.control["tool_result"]
    assert not result.is_error, result.content
    assert [event["phase"] for event in ui_install["fixture_trace"]] == ["renamed", "restored"]
    body = json.loads(json.loads(result.content[0].text)["body"])
    assert body["interaction"]["after"] == "Approved rename"
    assert body["interaction"]["restored"] == "Before verification"
    assert consume(verifier)["verdict"] == "pass"
    assert not ui_checks._pending, "the ephemeral backend object is removed after receipt creation"


def _short_plan():
    plan = _test_object_plan()
    plan["checks"][0]["timeout_seconds"] = 2
    return plan


@pytest.mark.parametrize("verifier", [_test_object_plan()], indirect=True)
def test_user_interrupt_removes_owned_test_resources_without_a_pass(verifier, ui_install):
    from openprogram.self_update import ui_checks
    ui_install["interrupt"] = True
    ui_install["native_capture"] = _native_driver(ui_install)
    verifier.run()
    assert verifier.control["tool_result"].is_error
    assert [event["phase"] for event in ui_install["fixture_trace"]] == ["renamed"]
    assert consume(verifier)["verdict"] == "inconclusive"
    assert not ui_checks._pending


@pytest.mark.parametrize("verifier", [_short_plan()], indirect=True)
@pytest.mark.parametrize("damage", ["object", "title", "restore", "unapproved"])
def test_test_object_cannot_expand_authority_or_pass_without_cleanup(verifier, ui_install, damage):
    from openprogram.self_update import ui_checks
    def corrupt(command):
        if damage == "object":
            command["object_id"] = "user-session"
        elif damage == "title":
            command["title"] = "not approved"
        elif damage == "restore" and command["op"] == "restore":
            command["title"] = "not restored"
        elif damage == "unapproved":
            command["action"] = "rename_session"
            command["session_id"] = "p1"
    ui_install["corrupt_command"] = corrupt
    ui_install["native_capture"] = _native_driver(ui_install)
    verifier.run()
    assert verifier.control["tool_result"].is_error
    assert consume(verifier)["verdict"] == "inconclusive"
    assert not ui_install.get("ws_mutations")
    assert not ui_checks._pending

"""A Windows package reaches the release only after it ran one tool call.

The Windows legs of the release install what they built, start it and send
one turn through a loopback stand-in model whose only reply is a command-tool
call; the command's UTF-8 output and exit code must reach the model. Only a
Windows runner runs these steps; here the workflow wiring and the stand-in
model are checked, and the smoke itself runs against this checkout.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
INSTALL_SMOKE = ROOT / ".github" / "workflows" / "windows-install-smoke.yml"
SMOKE = ROOT / "scripts" / "release" / "smoke-tool-call.py"
CONTRACTS = ROOT / "scripts" / "release" / "windows-contracts.txt"


def _smoke_module():
    spec = importlib.util.spec_from_file_location("smoke_tool_call", SMOKE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _step(workflow: str, name: str) -> str:
    match = re.search(rf"- name: {re.escape(name)}\n(.*?)(?=\n      - |\n  [a-z])", workflow, re.S)
    assert match, name
    return match.group(1)


def test_the_windows_app_is_verified_before_its_checksums():
    workflow = RELEASE.read_text(encoding="utf-8")
    # A file's checksum is recorded once, in release-manifest.json, by the
    # publish job from the uploaded artifacts; the desktop job uploads the
    # installer only after the smoke, and publish waits for that job.
    verify = workflow.index("- name: Verify the Windows App")
    assert verify < workflow.index("name: desktop-${{ matrix.layout }}-${{ matrix.platform }}-${{ matrix.arch }}")
    publish = workflow[workflow.index("\n  publish:"):]
    needs = re.search(r"needs: \[(.*?)\]", publish).group(1)
    assert "desktop" in [job.strip() for job in needs.split(",")]
    assert "- name: Create release manifest" in publish
    assert "SHA256SUMS" not in workflow
    step = _step(workflow, "Verify the Windows App")
    assert "if: matrix.platform == 'win'" in step
    assert "smoke-windows-desktop.ps1" in step and "-ToolCall" in step and "-Uninstall" in step
    # No marker file: a failed step uploads nothing, as for macOS and Linux.
    assert "desktop-verified" not in workflow


def test_windows_contracts_run_in_the_windows_leg_only():
    workflow = RELEASE.read_text(encoding="utf-8")
    step = _step(workflow, "Windows platform contracts")
    assert "if: matrix.platform == 'win'" in step
    assert "scripts/release/windows-contracts.txt" in step
    assert workflow.index("- name: Windows platform contracts") < workflow.index("- name: Build Windows installer")


def test_the_contract_list_covers_ci_and_names_real_tests():
    listed = [line.strip() for line in CONTRACTS.read_text(encoding="utf-8").splitlines()
              if line.strip() and not line.lstrip().startswith("#")]
    for entry in listed:
        assert (ROOT / entry.split("::", 1)[0]).exists(), entry
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    block = _step(ci, "Windows platform contracts")
    in_ci = re.findall(r"^\s+(tests/\S+)", block, re.M)
    assert in_ci and set(in_ci) <= set(listed)


def test_the_windows_command_line_install_runs_the_tool_call():
    workflow = RELEASE.read_text(encoding="utf-8")
    step = _step(workflow, "Install the same complete Windows runtime archive")
    assert "scripts/release/smoke-tool-call.py" in step
    assert step.index("--version") < step.index("smoke-tool-call.py")
    assert workflow.index(step) < workflow.index("- name: Record the verified install")


def test_x64_packages_are_also_installed_on_an_arm_runner():
    workflow = INSTALL_SMOKE.read_text(encoding="utf-8")
    job = workflow[workflow.index("  release-install:"):]
    assert "windows-11-vs2026-arm" in job and "windows-2025" in job
    assert "-ToolCall" in job and "smoke-tool-call.py" in job


def test_the_windows_base_records_the_bash_its_git_carries():
    # A machine without Git for Windows runs commands in the bundled Git's
    # bash when the dugite archive has one; base.json says which, or null.
    script = (ROOT / "scripts" / "release" / "build-base.ps1").read_text(encoding="utf-8")
    assert '@("bin\\bash.exe", "usr\\bin\\bash.exe")' in script
    assert "gitBash = $GitBash" in script
    assert script.index("$GitBash = $null") > script.index('Install-Git (Join-Path $Dest "git")')


def test_the_smoke_pipes_a_prompt_to_print_before_the_tool_call():
    text = SMOKE.read_text(encoding="utf-8")
    assert '"--print", "-"' in text and 'input="hi\\n"' in text
    assert text.index('"--print", "-"') < text.index("TURN_MARKER}: run the command tool once")


def test_the_desktop_smoke_script_stays_ascii():
    text = (ROOT / "scripts" / "release" / "smoke-windows-desktop.ps1").read_bytes()
    assert all(byte < 128 for byte in text)


def test_the_desktop_smoke_installs_under_a_group_policy_that_allows_signed_scripts_only():
    """The runner's own policy is unrestricted and -ExecutionPolicy Bypass beats
    every scope but Group Policy, so a fresh install that ran an unsigned .ps1
    passed the release gate and failed on company PCs."""
    text = (ROOT / "scripts" / "release" / "smoke-windows-desktop.ps1").read_text(encoding="ascii")
    policy = text.index('$PolicyKey = "HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\PowerShell"')
    assert "-Name ExecutionPolicy -PropertyType String -Value AllSigned" in text
    assert "Get-ExecutionPolicy -Scope MachinePolicy" in text
    install = text.index('Start-Process -FilePath $Installer -ArgumentList "/S", "/currentuser"')
    assert policy < install < text.index("Remove-Item -Path $PolicyKey", install)


def _post(base_url: str, messages: list, tools: list) -> dict:
    request = urllib.request.Request(
        f"{base_url}/chat/completions", method="POST",
        data=json.dumps({"messages": messages, "tools": tools}).encode())
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=5) as response:
        return json.loads(response.read())


def test_the_stand_in_model_calls_the_command_tool_once_then_answers():
    smoke = _smoke_module()
    tools = [{"type": "function", "function": {"name": "bash"}}]
    with smoke.StubModel() as model:
        user = {"role": "user", "content": f"{smoke.TURN_MARKER}: go"}
        first = _post(model.base_url, [user], tools)["choices"][0]["message"]
        call = first["tool_calls"][0]
        assert call["function"]["name"] == "bash"
        assert json.loads(call["function"]["arguments"]) == {"command": "echo 你好; exit 3"}
        result = {"role": "tool", "tool_call_id": call["id"],
                  "content": "你好\nProcess exited with code 3"}
        second = _post(model.base_url, [user, {"role": "assistant", "tool_calls": [call]}, result], tools)
        assert second["choices"][0]["message"]["content"] == smoke.FINAL_ANSWER
        assert smoke.check(model.turn, [{"tool": "bash"}], smoke.FINAL_ANSWER) == []


@pytest.mark.parametrize(("result", "missing"), [
    ("??\nProcess exited with code 3", "UTF-8 output"),
    ("你好\nProcess exited with code 1", "exit code 3"),
    ("你好\nexit_code=13", "exit code 3"),
])
def test_a_lost_text_or_exit_code_fails_the_smoke(result, missing):
    smoke = _smoke_module()
    turn = [{"kind": "call", "offered": ["bash"]}, {"kind": "result", "content": result}]
    problems = smoke.check(turn, [{"tool": "bash"}], smoke.FINAL_ANSWER)
    assert any(missing in problem for problem in problems), problems
    assert smoke.check([{"kind": "call", "offered": []}], [], "") != []


@pytest.mark.skipif(sys.platform == "win32", reason="runs this checkout's command on POSIX")
def test_the_smoke_passes_against_this_checkout(tmp_path):
    command = Path(sys.executable).with_name("openprogram")
    if not command.exists():
        pytest.skip("no openprogram command beside this Python")
    smoke = _smoke_module()
    assert smoke.main(["--openprogram", str(command), "--home", str(tmp_path), "--in-process",
                       "--timeout", "240"]) == 0


def test_a_running_worker_finds_the_provider_the_smoke_writes(tmp_path, monkeypatch):
    # The App's leg writes the stand-in provider while its worker already
    # runs; the worker reads the config again when a turn names a model it
    # does not know (runtime_management, agent.runtime.factory), so no
    # restart is needed. Same lookup, in this process, after it has started.
    from openprogram.models.providers.catalog import enabled_models
    from openprogram.models.providers.catalog.models import get_model
    from openprogram.state import config as user_config

    smoke = _smoke_module()
    monkeypatch.setattr(user_config, "get_config_path", lambda: tmp_path / "config.json")
    assert get_model(smoke.PROVIDER_ID, smoke.MODEL_ID) is None
    smoke.write_provider(tmp_path, "http://127.0.0.1:9/v1")
    try:
        assert enabled_models.register_model_from_config(smoke.PROVIDER_ID, smoke.MODEL_ID)
        model = get_model(smoke.PROVIDER_ID, smoke.MODEL_ID)
        assert model is not None and model.base_url.startswith("http://127.0.0.1:9")
    finally:
        enabled_models.ENABLED_MODELS.pop(f"{smoke.PROVIDER_ID}/{smoke.MODEL_ID}", None)

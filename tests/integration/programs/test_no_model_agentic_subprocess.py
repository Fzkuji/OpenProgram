"""Model-free agentic Programs execute in a real spawned child."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


def _workflow(state: Path, name: str, source: str) -> Path:
    """A committed user package; returns the checked copy it runs from."""
    project = state / "programs" / name
    project.mkdir(parents=True)
    (project / "__init__.py").write_text(
        f"from .workflow import {name}\nPROGRAM_ENTRIES = [{name}]\n"
    )
    (project / "workflow.py").write_text(source)
    for command in (
        ["git", "init", "-b", "main"],
        ["git", "add", "--all"],
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
         "commit", "-m", "Fixture"],
    ):
        subprocess.run(command, cwd=project, check=True, capture_output=True)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=project, check=True,
                            capture_output=True, text=True).stdout.strip()
    return state / "programs-cache" / name / commit


@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize("selection", ["missing", "without_credentials"])
def test_no_model_workflow_reaches_child_without_default_provider(
    tmp_path, use_model, selection
):
    state = tmp_path / ".openprogram"
    state.mkdir()
    project = _workflow(state, "no_model_probe", (
        "from pathlib import Path\n\n\n"
        "def no_model_probe(use_model: bool, runtime=None):\n"
        '    """No-model execution."""\n'
        "    Path(__file__).with_name('entered.flag').touch()\n"
        "    assert runtime.api_model is None\n"
        "    assert runtime.api_key is None\n"
        "    if use_model:\n"
        "        return runtime.exec('No configured model', tools=[])\n"
        "    return 'plain result'\n"
    ))
    if selection == "without_credentials":
        (state / "config.json").write_text(
            json.dumps(
                {
                    "providers": {
                        "openai": {
                            "enabled": True,
                            "models": [{"id": "fake-model", "name": "Fake"}],
                        }
                    }
                }
            )
        )
    script = """import asyncio, json, sys
from openprogram.runtime.context.session_db import default_db
from openprogram.runtime.agent.turn_request import TurnRequest
from openprogram.runtime.conversation.turn_runtime import _wrap_agentic_runtime_block
from openprogram.programs import get_agent_tool
db=default_db()
db.create_session("no-model", "main", source="test")
profile={} if sys.argv[2]=="missing" else {"model":"openai/fake-model"}
req=TurnRequest(session_id="no-model",user_text="",agent_id="main",source="test",profile_snapshot=profile)
tool=get_agent_tool("no_model_probe")
assert tool is not None
wrapped=_wrap_agentic_runtime_block(tool,req,lambda event: None,"anchor")
result=asyncio.run(wrapped.execute("call", {"use_model":sys.argv[1]=="True"},None,None))
print(json.dumps({"failed":result.is_error,"text":"".join(c.text for c in result.content)}))
"""
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "USERPROFILE": str(tmp_path),
        "CODEX_HOME": str(tmp_path / ".codex"),
        "OPENPROGRAM_CONFIG_DIR": str(state),
    }
    env.pop("OPENPROGRAM_PROFILE", None)
    result = subprocess.run(
        [sys.executable, "-c", script, str(use_model), selection],
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stderr
    assert (project / "entered.flag").is_file(), result.stdout
    outcome = json.loads(result.stdout.splitlines()[-1])
    assert outcome["failed"] is use_model
    if use_model:
        assert (
            "No model is configured" if selection == "missing" else "API key"
        ) in outcome["text"]
    else:
        assert "plain result" in outcome["text"]


@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize(
    "selection", ["missing", "provider_only", "model_only", "auto", "explicit"]
)
def test_forced_workflow_never_detects_an_unselected_provider(
    tmp_path, use_model, selection
):
    state = tmp_path / ".openprogram"
    state.mkdir()
    project = _workflow(state, "forced_probe", (
        "from pathlib import Path\n"
        "from openprogram.runtime.agent.runtime import Runtime\n"
        "from openprogram.models.providers.catalog import registry\n"
        "from openprogram.runtime.agent.runtime import factory\n\n"
        "def detect():\n"
        "    Path(__file__).with_name('auto-detect.flag').touch()\n"
        "    return 'unselected-fixture', 'fixture-model'\n\n"
        "def fake_call(*args, **kwargs):\n"
        "    Path(__file__).with_name('model-call.flag').touch()\n"
        "    return 'selected model result'\n\n"
        "def fake_runtime(provider, model, **kwargs):\n"
        "    Path(__file__).with_name('selected.txt').write_text(f'{provider}/{model}')\n"
        "    return Runtime(call=fake_call, model='fixture')\n\n"
        "registry.detect_provider = detect\n"
        "factory._api_routed_runtime = fake_runtime\n\n\n"
        "def forced_probe(use_model: bool, runtime=None):\n"
        '    """Forced model selection."""\n'
        "    Path(__file__).with_name('entered.flag').touch()\n"
        "    return runtime.exec('fixture', tools=[]) if use_model else 'plain result'\n"
    ))
    script = """import json, sys
from openprogram.runtime.context.session_db import default_db
from openprogram.runtime.conversation.turn_runtime import dispatch_forced_tool_call
from openprogram.programs import get_agent_tool
db=default_db()
db.create_session("forced-model", "main", source="test")
assert get_agent_tool("forced_probe") is not None
selected={
    "missing": {},
    "provider_only": {"provider":"selected-fixture"},
    "model_only": {"model":"fixture-model"},
    "auto": {"provider":"auto", "model":"fixture-model"},
    "explicit": {"provider":"selected-fixture", "model":"fixture-model"},
}[sys.argv[2]]
result=dispatch_forced_tool_call("forced-model", "anchor", "forced_probe",
    {"use_model":sys.argv[1]=="True"}, **selected)
print(json.dumps(result, default=str))
"""
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "USERPROFILE": str(tmp_path),
        "CODEX_HOME": str(tmp_path / ".codex"),
        "OPENPROGRAM_CONFIG_DIR": str(state),
    }
    env.pop("OPENPROGRAM_PROFILE", None)
    result = subprocess.run(
        [sys.executable, "-c", script, str(use_model), selection],
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stderr
    assert (project / "entered.flag").is_file(), result.stdout
    outcome = json.loads(result.stdout.splitlines()[-1])
    assert not (project / "auto-detect.flag").exists(), outcome
    assert outcome["ok"] is (selection == "explicit" or not use_model)
    assert (project / "model-call.flag").exists() is (
        selection == "explicit" and use_model
    )
    if selection == "explicit":
        assert (
            project / "selected.txt"
        ).read_text() == "selected-fixture/fixture-model"
    else:
        assert not (project / "selected.txt").exists()
        if use_model:
            assert "No model is configured" in json.dumps(outcome)

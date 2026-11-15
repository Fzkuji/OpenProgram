"""The environment a trial's worker starts with.

The Windows value is built here with ``windows=True``; it has not run on Windows.
"""
from openprogram.operations.self_update.trial import instance as trials
from openprogram.operations.self_update.versions import layout as paths
from tests.support.sealed_versions import BASE, make_layout


def _instance(tmp_path):
    layout = make_layout(tmp_path / "state" / "runtime")
    workspace = trials.workspace_for(layout, "u1").create()
    return trials.TrialInstance(workspace, layout, "v1", BASE, port=1, model_port=2, live_state=tmp_path / "state")


def test_a_windows_trial_finds_the_system_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "not-for-the-trial")
    instance = _instance(tmp_path)
    environment = instance.environment(windows=True, environ={"SystemRoot": r"D:\Windows"})
    entries = environment["PATH"].split(";")
    # cmd, taskkill, schtasks and where live in System32; PowerShell beside it.
    assert entries[1:] == [
        r"D:\Windows\System32", r"D:\Windows", r"D:\Windows\System32\Wbem",
        r"D:\Windows\System32\WindowsPowerShell\v1.0",
    ]
    assert entries[0] == str(paths.base_python(instance.layout.base(BASE)).parent), "the base's Python comes first"
    assert environment["SystemRoot"] == r"D:\Windows"
    assert "OPENAI_API_KEY" not in environment


def test_a_posix_trial_keeps_its_short_path(tmp_path):
    environment = _instance(tmp_path).environment(windows=False, environ={})
    assert environment["PATH"].split(":")[1:] == ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    assert "SystemRoot" not in environment

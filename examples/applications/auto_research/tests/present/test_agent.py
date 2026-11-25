"""paper_agent with the toolsmith scripted, on a tiny code repository with one kept result."""

import asyncio
import importlib.util
import json
import py_compile
import subprocess
import sys
from pathlib import Path

import pytest

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.present import agent as wf

METHOD = '''"""The method."""


def score(n: int, scale: float = 0.1) -> dict:
    """Run the method on n items and return its validation loss and accuracy."""
    return {"val_loss": n * scale, "acc": 73.5}


def _helper():
    return 1


def undocumented(x):
    return x
'''

SPEC = """# Experiment loop
Approved: yes
Command: python train.py
Metric: val_loss
Direction: lower
"""


@pytest.fixture
def paper(tmp_path):
    repo = tmp_path / "paper"
    (repo / "workflow").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "method.py").write_text(METHOD)
    (repo / "train.py").write_text("from method import score\nprint('val_loss:', score(5)['val_loss'])\n")
    (repo / "README.md").write_text("# Method\n\nCall `method.score(n)`.\n")
    (repo / "workflow" / "experiment.md").write_text(SPEC)
    (repo / "workflow" / "results.tsv").write_text(
        "commit\tmetric\tstatus\tseconds\tdescription\n"
        "abc1234def567\t0.5\tkeep\t3\tbaseline n=5\n"
        "fff0000\t0.9\tdiscard\t3\tworse\n")
    (repo / "workflow" / "evidence.csv").write_text(
        "id,claim,statement,source,script\nE1,C1,Accuracy is 73.5% on the test set.,results.tsv,\n")
    return repo


def tool(name, function="score", module="method", params=None, test=None):
    return {"name": name, "description": f"{name} tool.", "module": module, "function": function,
            "parameters": params if params is not None else [
                {"name": "n", "type": "int", "default": "", "description": "items"},
                {"name": "scale", "type": "float", "default": "0.1", "description": "scale"}],
            "test": test or {"arguments": '{"n": 5}', "output_key": "val_loss", "expected": 0.5,
                             "source": "results:abc1234def56"}}


def run(paper, reply, **kw):
    with script_roles({wf.toolsmith: [reply]}) as calls:
        out = json.loads(wf.paper_agent(str(paper), **kw))
    return out, calls


def test_tools_are_checked_written_and_reproduce_the_result(paper):
    reply = {"tools": [
        tool("run_method"),
        tool("accuracy", test={"arguments": '{"n": 1}', "output_key": "acc", "expected": 73.5,
                               "source": "evidence:E1"}),
        tool("bad_number", test={"arguments": '{"n": 1}', "output_key": "acc", "expected": 99.0,
                                 "source": "evidence:E1"}),
        tool("ghost", function="nowhere"),
        tool("no_n", params=[]),
    ]}
    out, calls = run(paper, reply)
    req = calls[wf.toolsmith][0]
    assert "Propose one to five tools" in req.text                  # docstring
    assert "Run the method on n items" in req.text and "results:abc1234def56" in req.text
    assert "undocumented" not in req.text and "_helper" not in req.text
    assert "fff0000" not in req.text                                 # discarded runs are not results
    assert out["tools"] == ["run_experiment", "run_method", "accuracy", "bad_number"]   # the Command is always a tool
    assert out["passed"] == 3 and out["failed"] == 0 and out["dropped"] == 3
    assert out["status"] == "PASS_WITH_NOTES"                          # tests dropped
    folder = Path(out["report"]).parent
    assert folder.parent == paper / "drafts" / "paper_agent"
    record = json.loads((folder / "agent.json").read_text())
    reasons = {d["tool"]: d["reason"] for d in record["dropped"]}
    assert "not a number of evidence:E1" in reasons["bad_number"]
    assert "not a top-level function" in reasons["ghost"]
    assert "needs n" in reasons["no_n"]
    assert [t["status"] for t in record["tests"]] == ["pass", "pass", "pass"]
    server = folder / "server.py"
    py_compile.compile(str(server), doraise=True)
    source = server.read_text()
    assert "from mcp.server.fastmcp import FastMCP" in source and "def tool_run_method(n: int, scale: float = 0.1)" in source
    readme = (folder / "README.md").read_text()
    assert f"claude mcp add paper -- {sys.executable} {server}" in readme and '"mcpServers"' in readme
    trail = json.loads(Path(out["run"]).read_text())
    assert [s["stage"] for s in trail["stages"]] == ["gather", "propose", "check", "test", "report"]


def test_server_lists_and_calls_the_tools(paper):
    pytest.importorskip("mcp.server.fastmcp")
    out, _ = run(paper, {"tools": [tool("run_method")]})
    assert out["status"] == "PASS"
    spec = importlib.util.spec_from_file_location("generated_server", out["server"])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    listed = asyncio.run(module.mcp.list_tools())
    assert [t.name for t in listed] == ["run_experiment", "run_method"]
    assert listed[1].inputSchema["required"] == ["n"]
    assert module.tool_run_method(n=5) == {"val_loss": 0.5, "acc": 73.5}


def test_a_result_that_does_not_come_back_fails(paper):
    (paper / "method.py").write_text(METHOD.replace("n * scale", "n * scale + 1"))
    out, _ = run(paper, {"tools": [tool("run_method")]})
    assert out["status"] == "FAIL" and out["failed"] == 2          # the method and the command it runs
    record = json.loads((Path(out["report"]).parent / "agent.json").read_text())
    assert all("got 1.5" in t["note"] for t in record["tests"])


def test_missing_dependencies_mean_the_tests_do_not_run(paper):
    (paper / "method.py").write_text("import surely_not_installed_pkg\n" + METHOD)
    out, _ = run(paper, {"tools": [tool("run_method")]})
    record = json.loads((Path(out["report"]).parent / "agent.json").read_text())
    assert out["status"] == "PASS_WITH_NOTES" and out["passed"] == 0 and out["failed"] == 0, record
    assert any("do not resolve" in n and "surely_not_installed_pkg" in n for n in record["notes"])
    py_compile.compile(out["server"], doraise=True)


def test_code_dir_from_rules_and_no_results(paper, tmp_path):
    code = tmp_path / "code"
    code.mkdir()
    (code / "lib.py").write_text(METHOD)
    (paper / "workflow" / "rules.json").write_text(json.dumps({"code": {"repo": str(code)}}))
    assert wf.code_folder(paper, "") == code
    (paper / "workflow" / "rules.json").write_text(json.dumps({"code": {"repo": "https://github.com/a/b"}}))
    assert wf.code_folder(paper, "") == paper
    (paper / "workflow" / "results.tsv").unlink()
    (paper / "workflow" / "evidence.csv").unlink()
    with pytest.raises(ValueError, match="no kept run"):
        wf.paper_agent(str(paper))


def test_functions_index_skips_private_tests_and_venvs(paper):
    (paper / "tests").mkdir()
    (paper / "tests" / "test_x.py").write_text("def test_a():\n    'doc'\n")
    (paper / ".venv" / "lib").mkdir(parents=True)
    (paper / ".venv" / "lib" / "m.py").write_text("def f():\n    'doc'\n")
    index = wf.functions(paper)
    assert set(index) == {"method.score", "method.undocumented"}
    assert index["method.score"]["params"] == [{"name": "n", "required": True}, {"name": "scale", "required": False}]


def test_a_script_is_not_wrapped_and_the_command_becomes_a_tool():
    import ast as _ast
    from auto_research.tasks.present import agent as ag
    script = _ast.parse("import argparse\np = argparse.ArgumentParser()\na = p.parse_args()\ndef sample(n):\n    return n\n")
    library = _ast.parse('"""doc"""\nimport math\nRATE = 0.1\ndef f(x):\n    return x\nif __name__ == "__main__":\n    f(1)\n')
    assert ag.runs_on_import(script) == 2 and ag.runs_on_import(library) == 0
    refs = {"results:aaa": {"id": "results:aaa", "numbers": ["0.5"]}, "results:bbb": {"id": "results:bbb", "numbers": ["0.4"]}}
    tool = ag.run_tool({"Command": "python train.py", "Metric": "val_loss", "Direction": "lower"}, refs)
    assert tool["name"] == "run_experiment" and tool["test"]["expected"] == 0.4 and tool["test"]["source"] == "results:bbb"
    assert ag.run_tool({"Metric": "val_loss"}, refs) is None

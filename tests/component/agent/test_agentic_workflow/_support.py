"""Behavior tests for self-programmed task-list workflows."""


from __future__ import annotations


import json


import inspect


import multiprocessing as mp


import re


import subprocess


import textwrap


import threading


from types import SimpleNamespace


from pathlib import Path


import pytest


import openprogram.programs as programs


from tests.support.workflow_tl import TL


def _code(body: str, helpers: str = "") -> str:
    source = textwrap.dedent(helpers).strip()
    if source:
        source += "\n\n"
    source += "def workflow():\n" + textwrap.indent(
        textwrap.dedent(body).strip(), "    "
    )
    return f"```python\n{source}\n```"


def _project_entry(body: str) -> str:
    source = TL._validated_reply(_code(body)).replace(
        "def workflow():", "def research_workflow(task):", 1,
    )
    return (
        "from openprogram.agentic_programming import agentic_function\n\n"
        "@agentic_function\n"
        + source
    )


def _project(
    *,
    name: str = "research_workflow",
    summary: str = "Research and synthesize a topic",
    tags: list[str] | None = None,
    readme: str = "# Research workflow\n\nReusable research steps.\n",
    files: dict[str, str] | None = None,
) -> str:
    project_files = dict(files or {
        "steps/discover.py": (
            "def discover(task):\n"
            "    return agent('discover papers')\n"
        ),
        "entry.py": "def workflow(task):\n    return discover(task)\n",
    })
    if "entry.py" in project_files:
        project_files["entry.py"] = project_files["entry.py"].replace(
            "def workflow():", "def workflow(task):", 1,
        )
        entrypoint = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")
        entrypoint = entrypoint or "generated_workflow"
        entry_source = project_files.pop("entry.py").replace(
            "def workflow(", f"def {entrypoint}(", 1,
        )
        imports = []
        for path, source in sorted(project_files.items()):
            if not path.startswith(("steps/", "goals/", "helpers/")):
                continue
            names = re.findall(r"(?m)^def ([a-zA-Z_][a-zA-Z0-9_]*)\(", source)
            if names:
                module = path[:-3].replace("/", ".")
                imports.append(
                    f"from .{module} import {', '.join(names)}"
                )
        project_files.update({
            "__init__.py": (
                f"from .workflow import {entrypoint}\n\n"
                f"__all__ = [{entrypoint!r}]\n"
            ),
            "workflow.py": (
                "from openprogram.agentic_programming import agentic_function\n"
                + ("\n".join(imports) + "\n" if imports else "")
                + "\n@agentic_function\n"
                + entry_source
            ),
            "steps/__init__.py": project_files.get("steps/__init__.py", ""),
            "tests/test_workflow.py": (
                f"from workflows.{entrypoint} import {entrypoint}\n\n"
                "def test_entrypoint_is_callable():\n"
                f"    assert callable({entrypoint})\n"
            ),
        })
        name = entrypoint
    return json.dumps({
        "project_metadata": {
            "name": name,
            "summary": summary,
            "tags": tags or ["research"],
        },
        "readme": readme,
        "files": project_files,
    }, ensure_ascii=False)


def _package_project() -> str:
    return json.dumps({
        "project_metadata": {
            "name": "literature_review",
            "summary": "Research and synthesize a topic",
            "tags": ["research"],
        },
        "readme": "# Literature review\n\nReusable research workflow.\n",
        "files": {
            "__init__.py": (
                "from .workflow import literature_review\n\n"
                "__all__ = ['literature_review']\n"
            ),
            "workflow.py": (
                "from openprogram.agentic_programming import agentic_function\n"
                "from .steps.discover import discover\n\n"
                "@agentic_function\n"
                "def literature_review(task: str):\n"
                "    return discover(task)\n"
            ),
            "steps/__init__.py": "",
            "steps/discover.py": (
                "from openprogram.agentic_programming import agent\n\n"
                "def discover(task: str):\n"
                "    return agent('discover ' + task)\n"
            ),
            "tests/test_workflow.py": (
                "from workflows.literature_review import literature_review\n\n"
                "def test_entrypoint_is_callable():\n"
                "    assert callable(literature_review)\n"
            ),
        },
    }, ensure_ascii=False)


@pytest.fixture
def session_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # These tests own planning, revisions and runtime behavior; the real OS
    # publication gate is covered by integration/programs/test_workflow_authoring.py.
    from openprogram.programs.workflow._project import authoring
    monkeypatch.setattr(authoring, "_run_tests", lambda *_args: {"executed_tests": True, "sandboxed": True})
    monkeypatch.setattr(TL, "_session_repo", lambda _sid: tmp_path)
    monkeypatch.setattr(TL, "_workflow_projects_root", lambda: tmp_path / "catalog")
    monkeypatch.setattr(TL, "_registered_agentic_functions", lambda: {})
    monkeypatch.setattr(TL, "current_session_id", lambda: "s1")
    return tmp_path


def _planner(monkeypatch: pytest.MonkeyPatch, *replies: str) -> list[str]:
    prompts: list[str] = []
    queue = list(replies)
    pending_legacy: list[str] = []

    def project_reply(reply: str, prompt: str) -> str:
        if reply.strip() == "SINGLE":
            task = prompt.split("<task>\n", 1)[1].split("\n</task>", 1)[0]
            return _project(files={
                "steps/task.py": (
                    "def run_task(task):\n"
                    f"    return agent({(task + chr(10) + chr(10) + TL.DELIVERY_INSTRUCTIONS)!r})\n"
                ),
                "entry.py": "def workflow(task):\n    return run_task(task)\n",
            })
        source = TL._extract_source(reply)
        if "def workflow():" not in source:
            return _project(files={
                "steps/placeholder.py": "def project_marker():\n    return None\n",
                "entry.py": source,
            })
        helper = source.replace("def workflow():", "def run_workflow_step():", 1)
        return _project(files={
            "steps/legacy.py": helper,
            "entry.py": "def workflow(task):\n    return run_workflow_step()\n",
        })

    def fake(_sid, prompt, **_kwargs):
        prompts.append(prompt)
        if pending_legacy:
            return project_reply(pending_legacy.pop(), prompt)
        if not queue:
            raise AssertionError("unexpected planner call")
        reply = queue.pop(0)
        if "<workflow project candidates>" in prompt and (
            reply.strip() == "SINGLE" or re.search(r"```python", reply)
        ):
            pending_legacy.append(reply)
            return json.dumps({"action": "create"})
        if reply.strip() == "SINGLE" or (
            "<workflow project candidates>" not in prompt
            and re.search(r"```python", reply)
        ):
            return project_reply(reply, prompt)
        return reply

    monkeypatch.setattr(TL, "_run_planner_turn", fake)
    return prompts


def _executor(monkeypatch: pytest.MonkeyPatch, fn=None) -> list[dict]:
    calls: list[dict] = []

    def fake(prompt, description="", agent_id="", start_from="clean",
             run_in_background=False, to="", archive_when_done=False):
        kwargs = {
            "description": description, "agent_id": agent_id,
            "start_from": start_from, "run_in_background": run_in_background,
            "to": to, "archive_when_done": archive_when_done,
        }
        calls.append({"prompt": prompt, **kwargs})
        return fn(prompt, kwargs) if fn else f"done: {prompt}"

    monkeypatch.setattr(TL, "_agent_function", lambda session_id, spawn_caller: fake)
    monkeypatch.setattr(TL, "_agent_loop_function", lambda: fake)
    return calls


def _llm_executor(monkeypatch: pytest.MonkeyPatch, fn=None) -> list[dict]:
    calls: list[dict] = []

    def fake(prompt, *, model="", effort="", response_format=None,
             choices=None, web_search=False, timeout_s=None):
        if "<workflow_summary>" in prompt:
            return {"summary": "Completed the workflow.", "return_result": False}
        kwargs = {
            "model": model, "effort": effort,
            "response_format": response_format, "choices": choices,
            "web_search": web_search, "timeout_s": timeout_s,
        }
        calls.append({"prompt": prompt, **kwargs})
        return fn(prompt, kwargs) if fn else f"done: {prompt}"

    monkeypatch.setattr(TL, "_llm_function", lambda: fake)
    return calls


def _summarizer(
    monkeypatch: pytest.MonkeyPatch,
    summary: str = "Completed the requested work.",
    *,
    return_result: bool = False,
) -> list[dict]:
    calls: list[dict] = []

    def fake(prompt, **kwargs):
        calls.append({"prompt": prompt, **kwargs})
        return {"summary": summary, "return_result": return_result}

    monkeypatch.setattr(TL, "_llm_function", lambda: fake)
    return calls


def _instance(repo: Path, run_id: str) -> Path:
    return repo / "workflows" / run_id


def _snapshot_package(repo: Path, run_id: str, name: str = "research_workflow") -> Path:
    return _instance(repo, run_id) / "snapshot" / "workflows" / name


def _state(repo: Path, run_id: str) -> dict:
    return json.loads(
        (_instance(repo, run_id) / "state.json").read_text(encoding="utf-8")
    )


def _install_workflow_project(repo: Path, reply: str) -> tuple[dict, str]:
    candidate = TL._validate_project_candidate(json.loads(reply))
    name = candidate["project_metadata"]["entrypoint"]
    instance = repo / "project-fixtures" / name
    instance.mkdir(parents=True)
    TL._replace_snapshot(instance, candidate)
    project_id, revision = TL._publish_snapshot(
        instance,
        project_id="",
        action="create",
        metadata=candidate["project_metadata"],
    )
    assert project_id == name
    return candidate, revision


def _git_output(project: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(project), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _dir_bytes(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _run_task(task: str) -> dict:
    """Author one published workflow, then execute it. Returns the run payload."""
    created = TL.create_workflow(task)
    return TL._run_published_workflow(
        task,
        created["workflow_id"],
        created["revision"],
        session_id=TL.current_session_id(),
        spawn_caller=None,
    )


def _install_legacy_project(repo: Path, name: str) -> None:
    project = repo / "catalog" / name
    project.mkdir(parents=True)
    TL._git(project, "init", "-b", "main")
    (project / "pyproject.toml").write_text(
        "[project]\n"
        f'name = "{name}"\n'
        'version = "0.1.0"\n'
        'description = "Legacy research workflow"\n'
        'keywords = ["research"]\n\n'
        "[tool.openprogram]\n"
        f'display-name = "{name}"\n',
        encoding="utf-8",
    )
    TL._git(project, "add", "--all")
    TL._git(
        project,
        "-c", "user.name=OpenProgram",
        "-c", "user.email=openprogram@localhost",
        "commit", "-m", "legacy fixture",
    )


def _workflow_run_states(session_repo: Path) -> list[dict]:
    runs_dir = session_repo / "workflows"
    if not runs_dir.exists():
        return []
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in runs_dir.glob("*/state.json")
    ]


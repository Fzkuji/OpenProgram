from openprogram.interfaces.server.routes.catalog import programs as programs_routes


def test_package_calls_follow_current_functions_not_repository_imports(
    tmp_path, monkeypatch
):
    root = tmp_path / "programs" / "example"
    package = root / "example"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    entry = package / "main.py"
    entry.write_text(
        "from openprogram import Agent\nfrom example.actions import dispatch\nclass RunAgent(Agent):\n    method_options = {\n        'run': {\n            'name': 'run',\n            'tool': True\n        },\n    }\n\n    def run(self, task):\n        return dispatch(task)\n\n\nrun = RunAgent().run\n"
    )
    actions = package / "actions.py"
    actions.write_text(
        'def dispatch(name):\n    if name == "computer":\n        return computer_use()\n    return browser_use()\ndef computer_use(): pass\ndef browser_use(): pass\n'
    )
    benchmarks = root / "benchmarks"
    benchmarks.mkdir()
    for index in range(205):
        (benchmarks / f"{index}.py").write_text("import unrelated\n")
    monkeypatch.setattr(programs_routes, "_entity_paths", lambda: {"applications/example": root})
    monkeypatch.setattr(programs_routes, "_applications", lambda: {
        "example": programs_routes._Application("example", root, "installed", "harness"),
    })
    first = programs_routes._program_logic("applications/example")
    assert first["nodes"][0]["program_kind"] == "application"
    assert first["nodes"][0]["entity_kind"] == "harness"
    assert {"run", "dispatch", "computer_use", "browser_use"} <= {
        n["name"] for n in first["nodes"]
    }
    assert "source_file_limit" not in first["analysis_warnings"]
    assert any(e.get("kind") == "conditional" for e in first["edges"])
    actions.write_text("def dispatch(name):\n    return vm_use()\ndef vm_use(): pass\n")
    second = programs_routes._program_logic("applications/example")
    names = {n["name"] for n in second["nodes"]}
    assert "vm_use" in names
    assert "computer_use" not in names


def test_package_entry_filter_excludes_other_decorated_functions(tmp_path):
    from openprogram.interfaces.server.routes.execution.program_calls import package_calls

    package = tmp_path / "sample"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "main.py").write_text(
        "from openprogram import Agent\nclass RunAgent(Agent):\n    method_options = {\n        'run': {\n            'name': 'run',\n            'tool': True\n        },\n    }\n\n    def run(self):\n        return helper()\n\n\nrun = RunAgent().run\nclass UnusedAgent(Agent):\n    method_options = {\n        'unused': {\n            'name': 'unused',\n            'tool': True\n        },\n    }\n\n    def unused(self): pass\n\n\nunused = UnusedAgent().unused\ndef helper(): pass\n"
    )
    result = package_calls(tmp_path, "applications/sample", "run")
    assert {n["name"] for n in result["nodes"][1:]} == {"run", "helper"}


def test_package_analysis_never_executes_code_or_follows_symlinks(tmp_path):
    from openprogram.interfaces.server.routes.execution.program_calls import package_calls

    package = tmp_path / "sample"
    package.mkdir()
    (package / "__init__.py").write_text('raise RuntimeError("must not execute")')
    (package / "main.py").write_text(
        "from openprogram import Agent\nclass RunAgent(Agent):\n    method_options = {\n        'run': {\n            'name': 'run',\n            'tool': True\n        },\n    }\n\n    def run(self):\n        return actions[\"unknown\"]()\n\n\nrun = RunAgent().run\n"
    )
    (package / "outside.py").symlink_to("/etc/passwd")
    result = package_calls(tmp_path, "applications/sample", "run")
    assert result["analysis_warnings"] == ["unresolved_dynamic_call"]


def test_local_callable_alias_is_reported_as_unresolved(tmp_path):
    from openprogram.interfaces.server.routes.execution.program_calls import package_calls

    package = tmp_path / "sample"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "main.py").write_text(
        "from openprogram import Agent\nclass RunAgent(Agent):\n    method_options = {\n        'run': {\n            'name': 'run',\n            'tool': True\n        },\n    }\n\n    def run(self, spec):\n        func = spec[\"function\"]\n        return func()\n\n\nrun = RunAgent().run\n"
    )
    result = package_calls(tmp_path, "applications/sample", "run")
    assert result["analysis_warnings"] == ["unresolved_dynamic_call"]

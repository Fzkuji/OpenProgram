from openprogram.webui.routes import programs


def test_application_calls_follow_current_functions_not_repository_imports(tmp_path, monkeypatch):
    root = tmp_path / 'applications' / 'example'
    package = root / 'example'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('')
    entry = package / 'main.py'
    entry.write_text('from example.actions import dispatch\n@agentic_function()\ndef run(task):\n    return dispatch(task)\n')
    actions = package / 'actions.py'
    actions.write_text('def dispatch(name):\n    if name == "computer":\n        return computer_use()\n    return browser_use()\ndef computer_use(): pass\ndef browser_use(): pass\n')
    benchmarks = root / 'benchmarks'
    benchmarks.mkdir()
    for index in range(205):
        (benchmarks / f'{index}.py').write_text('import unrelated\n')
    monkeypatch.setattr(programs, '_entity_paths', lambda: {'applications/example': root})
    monkeypatch.setattr(programs, '_catalog_roots', lambda: [tmp_path])
    first = programs._program_logic('applications/example')
    assert {'run', 'dispatch', 'computer_use', 'browser_use'} <= {n['name'] for n in first['nodes']}
    assert 'source_file_limit' not in first['analysis_warnings']
    assert any(e.get('kind') == 'conditional' for e in first['edges'])
    actions.write_text('def dispatch(name):\n    return vm_use()\ndef vm_use(): pass\n')
    second = programs._program_logic('applications/example')
    names = {n['name'] for n in second['nodes']}
    assert 'vm_use' in names
    assert 'computer_use' not in names


def test_application_entry_filter_excludes_other_decorated_functions(tmp_path):
    from openprogram.webui.routes.program_calls import application_calls

    package = tmp_path / 'sample'
    package.mkdir()
    (package / '__init__.py').write_text('')
    (package / 'main.py').write_text(
        '@agentic_function()\ndef run():\n    return helper()\n'
        '@agentic_function()\ndef unused(): pass\ndef helper(): pass\n'
    )
    result = application_calls(tmp_path, 'applications/sample', 'run')
    assert {n['name'] for n in result['nodes'][1:]} == {'run', 'helper'}


def test_application_analysis_never_executes_code_or_follows_symlinks(tmp_path):
    from openprogram.webui.routes.program_calls import application_calls

    package = tmp_path / 'sample'
    package.mkdir()
    (package / '__init__.py').write_text('raise RuntimeError("must not execute")')
    (package / 'main.py').write_text('@agentic_function()\ndef run():\n    return actions["unknown"]()\n')
    (package / 'outside.py').symlink_to('/etc/passwd')
    result = application_calls(tmp_path, 'applications/sample', 'run')
    assert result['analysis_warnings'] == ['unresolved_dynamic_call']


def test_local_callable_alias_is_reported_as_unresolved(tmp_path):
    from openprogram.webui.routes.program_calls import application_calls

    package = tmp_path / 'sample'
    package.mkdir()
    (package / '__init__.py').write_text('')
    (package / 'main.py').write_text(
        '@agentic_function()\ndef run(spec):\n    func = spec["function"]\n    return func()\n'
    )
    result = application_calls(tmp_path, 'applications/sample', 'run')
    assert result['analysis_warnings'] == ['unresolved_dynamic_call']

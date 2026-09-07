import json
from types import SimpleNamespace
import pytest
from openprogram.worker import lifecycle


def test_worker_uses_declared_named_runtime(tmp_path, monkeypatch):
    root = tmp_path / 'runtime'
    python = root / 'python/bin/python3'
    python.parent.mkdir(parents=True)
    python.touch()
    helper = root / 'OpenProgram.app/Contents/MacOS/OpenProgram'
    helper.parent.mkdir(parents=True)
    helper.write_text('helper')
    helper.chmod(0o755)
    (root / 'runtime-manifest.json').write_text(json.dumps({'python': 'python/bin/python3', 'worker_python': str(helper.relative_to(root))}))
    monkeypatch.setattr(lifecycle.sys, 'platform', 'darwin')
    monkeypatch.setattr(lifecycle.sys, 'executable', str(python))
    command = lifecycle._detached_worker_command(SimpleNamespace(isolated=1, dont_write_bytecode=1))
    assert command[0] == str(helper)
    assert command[1:3] == ['-I', '-B']
    helper.unlink()
    with pytest.raises(RuntimeError, match='runtime'):
        lifecycle._detached_worker_command()


def test_linux_keeps_its_existing_interpreter(monkeypatch):
    monkeypatch.setattr(lifecycle.sys, 'platform', 'linux')
    monkeypatch.setattr(lifecycle.sys, 'executable', '/example/python')
    assert lifecycle._detached_worker_command()[0] == '/example/python'


def test_worker_rejects_helper_outside_runtime(tmp_path, monkeypatch):
    root = tmp_path / 'runtime'
    python = root / 'python/bin/python3'
    python.parent.mkdir(parents=True)
    python.touch()
    (root / 'runtime-manifest.json').write_text(json.dumps({'python': 'python/bin/python3', 'worker_python': '../outside'}))
    monkeypatch.setattr(lifecycle.sys, 'platform', 'darwin')
    monkeypatch.setattr(lifecycle.sys, 'executable', str(python))
    with pytest.raises(RuntimeError, match='escapes'):
        lifecycle._detached_worker_command()


def test_direct_cli_reexec_keeps_arguments(monkeypatch):
    calls = []
    monkeypatch.setattr(lifecycle, 'worker_executable', lambda: '/managed/OpenProgram')
    monkeypatch.setattr(lifecycle.sys, 'executable', '/managed/python')
    monkeypatch.setattr(lifecycle.sys, 'argv', ['openprogram', 'programs', 'run', 'gui_agent', '-a', 'task=hello world'])
    monkeypatch.setattr(lifecycle.os, 'execv', lambda exe, argv: calls.append((exe, argv)))
    lifecycle.use_named_runtime_for_cli()
    assert calls[0][0] == '/managed/OpenProgram'
    assert calls[0][1][-7:] == ['-m', 'openprogram', 'programs', 'run', 'gui_agent', '-a', 'task=hello world']


def test_cli_preserves_unbuffered_interpreter_not_business_flags(monkeypatch):
    calls = []
    monkeypatch.setattr(lifecycle, 'worker_executable', lambda: '/managed/OpenProgram')
    monkeypatch.setattr(lifecycle.sys, 'executable', '/managed/python')
    monkeypatch.setattr(lifecycle.sys, 'argv', ['openprogram', 'worker', 'run'])
    monkeypatch.setattr(lifecycle.sys, 'orig_argv', ['/managed/python', '-I', '-B', '-u', '-X', 'utf8', '-m', 'openprogram', 'worker', 'run'])
    monkeypatch.setattr(lifecycle.os, 'execv', lambda exe, argv: calls.append(argv))
    lifecycle.use_named_runtime_for_cli()
    assert '-u' in calls[-1][:calls[-1].index('-m')]
    assert calls[-1][1:6] == ['-I', '-B', '-u', '-X', 'utf8']
    monkeypatch.setattr(lifecycle.sys, 'orig_argv', ['/managed/python', '-m', 'openprogram', 'example', '-u'])
    lifecycle.use_named_runtime_for_cli()
    assert '-u' not in calls[-1][:calls[-1].index('-m')]


@pytest.mark.parametrize("options", [["-u", "-mopenprogram"], ["-Iumopenprogram"], ["-Iuc", "ignored code"], ["-uWignore", "-mopenprogram"], ["-uW", "ignore", "-mopenprogram"]])
def test_cli_compact_module_option_stops_interpreter_scan(monkeypatch, options):
    calls = []
    monkeypatch.setattr(lifecycle, 'worker_executable', lambda: '/managed/OpenProgram')
    monkeypatch.setattr(lifecycle.sys, 'executable', '/managed/python')
    monkeypatch.setattr(lifecycle.sys, 'argv', ['openprogram', '--help'])
    monkeypatch.setattr(lifecycle.sys, 'orig_argv', ['/managed/python', *options, '--help'])
    monkeypatch.setattr(lifecycle.os, 'execv', lambda exe, argv: calls.append(argv))
    lifecycle.use_named_runtime_for_cli()
    assert not any('mopenprogram' in item or item == 'ignored code' for item in calls[0])
    assert any('u' in item for item in calls[0][1:-3])
    assert calls[0][-3:] == ['-m', 'openprogram', '--help']

"""Permission diagnostics are executable CLI dry runs, never tool executions."""
import json
import subprocess
import sys


def invoke(*args):
    return subprocess.run([sys.executable, '-m', 'openprogram', 'permissions', *args],
                          capture_output=True, text=True, timeout=15)


def test_cli_explains_strictest_rule_without_execution(tmp_path):
    rules = tmp_path / 'rules.json'
    rules.write_text(json.dumps({'allow': ['bash'], 'deny': ['bash(touch:*)']}))
    target = tmp_path / 'must-not-exist'
    result = invoke('check', '--rules', str(rules), '--tool', 'bash',
                    '--args', json.dumps({'command': f'touch {target}'}), '--expect', 'deny')
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value['decision'] == 'deny'
    assert [m['decision'] for m in value['matches']] == ['deny', 'allow']
    assert value['scope'] == 'permission_rules' and not target.exists()


def test_cli_policy_cases_and_invalid_input(tmp_path):
    rules, cases = tmp_path / 'rules.json', tmp_path / 'cases.json'
    rules.write_text(json.dumps({'allow': ['bash(git:*)']}))
    cases.write_text(json.dumps([
        {'tool': 'bash', 'args': {'command': 'git status'}, 'expected': 'allow'},
        {'tool': 'bash', 'args': {'command': 'PATH=/evil git status'}, 'expected': 'unmatched'},
        {'tool': 'bash', 'args': {'command': 'git status && rm -rf /'}, 'expected': 'unmatched'},
    ]))
    result = invoke('test', '--rules', str(rules), '--cases', str(cases))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['passed'] == 3
    cases.write_text(json.dumps([{'tool': 'bash', 'args': {'command': 'git status'}, 'expected': 'deny'}]))
    assert invoke('test', '--rules', str(rules), '--cases', str(cases)).returncode == 1
    rules.write_text('{"allow":"bash"}')
    assert invoke('test', '--rules', str(rules), '--cases', str(cases)).returncode == 2

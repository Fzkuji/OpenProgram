"""Registered browser receipts retain existing parent scope across actual spawn."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


def _probe(tmp_path, scenario):
    root = Path(__file__).resolve().parents[3]
    receipt = tmp_path / 'receipt.json'
    env = dict(os.environ, HOME=str(tmp_path), USERPROFILE=str(tmp_path),
               OPENPROGRAM_CONFIG_DIR=str(tmp_path / '.openprogram'),
               PAGE_PROBE_RECEIPT=str(receipt), PAGE_PROBE_SCENARIO=scenario, PYTHONPATH=str(root),
               CODEX_HOME=str(tmp_path / '.codex'))
    env.pop('OPENPROGRAM_PROFILE', None)
    run = subprocess.run([sys.executable, str(root / 'tests/support/program_page_scope_probe.py')],
                         cwd=root, env=env, text=True, capture_output=True, timeout=45)
    assert run.returncode == 0, run.stdout + run.stderr
    data = json.loads(receipt.read_text())
    assert 'outer_error' not in data, data
    assert data['pid'] != data['parent_pid']
    assert data['interaction'] == 'non-interactive'
    assert data['speaker'] == 'runtime'
    assert data['parent_identity'] == data['source_identity']
    return data


def test_program_inherits_real_registered_page_scope(tmp_path):
    data = _probe(tmp_path, 'available')
    assert data['listed_error'] is False, data
    assert 'Owned Page' in data['listed_text'], data
    assert data['child_scope'] == data['inner_scope']
    assert any(c['op'] == 'list' for c in data['renderer_calls'])
    assert data['remaining_bindings'] == 0


@pytest.mark.parametrize('scenario', ['disabled', 'no_app', 'capture_fail', 'deny', 'ask', 'empty'])
def test_program_page_scope_does_not_create_or_override_grants(tmp_path, scenario):
    data = _probe(tmp_path, scenario)
    assert data['listed_error'] is True, data
    assert 'Owned Page' not in data['listed_text'], data
    assert data['remaining_bindings'] == 0
    assert not any(c['op'] in {'activate', 'resolve'} for c in data['renderer_calls'])
    if scenario == 'disabled':
        assert data['child_scope'] == data['inner_scope'] == {'surfaces': [{'enabled': False}]}
    elif scenario == 'empty':
        assert data['child_scope'] == data['inner_scope'] == {}
    else:
        assert data['child_scope'] is data['inner_scope'] is None
    if scenario != 'capture_fail':
        assert data['renderer_calls'] == []


@pytest.mark.parametrize('scenario', ['existing', 'stale_binding', 'plan'])
def test_existing_page_scope_and_constraints_survive_spawn(tmp_path, scenario):
    data = _probe(tmp_path, scenario)
    assert data['child_scope'] == data['inner_scope']
    assert data['child_scope']['surfaces'][0]['window_id'] == 'owned-window'
    assert data['remaining_bindings'] == 0
    if scenario in {'existing', 'stale_binding'}:
        assert data['listed_error'] is False, data
        assert 'Owned Page' in data['listed_text'], data
        assert all(call['fixture_window_id'] == 'owned-window' for call in data['renderer_calls'])
    else:
        assert data['listed_error'] is True, data
        assert 'Owned Page' not in data['listed_text'], data
        assert not any(c['op'] in {'activate', 'resolve'} for c in data['renderer_calls'])
    if scenario == 'plan':
        assert data['child_mode'] == data['inner_mode'] == 'plan'


@pytest.mark.parametrize('scenario', ['active_plan', 'live_plan', 'live_deny', 'live_ask'])
def test_effective_parent_policy_survives_existing_page_scope(tmp_path, scenario):
    data = _probe(tmp_path, scenario)
    assert data['child_scope'] == data['inner_scope']
    assert data['listed_error'] is True, data
    assert 'Owned Page' not in data['listed_text'], data
    assert data['remaining_bindings'] == 0
    assert not any(c['op'] in {'activate', 'resolve'} for c in data['renderer_calls'])
    if scenario in {'active_plan', 'live_plan'}:
        assert data['parent_decision'][:2] == ['deny', 'PLAN_MODE_DENY']
        assert data['child_mode'] == data['inner_mode'] == 'plan'
    elif scenario == 'live_deny':
        assert data['parent_decision'][:2] == ['deny', 'RULE_DENY']
        assert '[blocked by rule]' in data['listed_text']
    else:
        assert data['parent_decision'][:2] == ['ask', 'RULE_ASK']
        assert 'no one to approve' in data['listed_text']


def test_new_owner_page_scope_survives_old_interrupted_effect(tmp_path):
    data = _probe(tmp_path, 'old_recovery')
    assert data['listed_error'] is False, data
    assert 'Owned Page' in data['listed_text'], data
    assert data['action_policy'] == ['auto', 'AUTO_CLASSIFY'], data
    assert data['child_mode'] == data['inner_mode'] == 'auto'
    assert data['remaining_bindings'] == 0

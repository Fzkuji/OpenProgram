"""Current owner sources survive actual program spawn without granting authority."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('fault', ['', 'foreign', 'assistant', 'replay', 'other_store', 'other_db', 'other_execution'])
def test_public_admission_spawn_source_identity(tmp_path, fault):
    root = Path(__file__).resolve().parents[4]
    core = os.environ.get('OWNER_PROBE_CORE', str(root))
    receipt = tmp_path / 'receipt.json'
    env = dict(os.environ, HOME=str(tmp_path), USERPROFILE=str(tmp_path),
               OWNER_PROBE_RECEIPT=str(receipt), OWNER_PROBE_FAULT=fault,
               PYTHONPATH=core)
    run = subprocess.run([sys.executable, str(root / 'tests/support/weekly_owner_source_probe.py')],
                         cwd=root, env=env, text=True, capture_output=True, timeout=45)
    assert run.returncode == 0, run.stdout + run.stderr
    data = json.loads(receipt.read_text())
    turn = data['turns'][-1]
    child = turn['output']
    assert turn['status'] == 'completed'
    assert child['pid'] != data['parent_pid']
    assert child['runtime_speaker'] == 'runtime'
    assert child['interaction'] == 'non-interactive'
    if fault:
        assert child['rows'] == []
    else:
        assert len(child['rows']) == 1
        original = child['rows'][0]
        current = turn['canonical_users'][-1]
        assert original['text'] == turn['task'] == current['content']
        assert original['source'] == f"conversation:{turn['ack']['data']['session_id']}#{current['id']}"
        assert original['current_supplied_owner'] is True

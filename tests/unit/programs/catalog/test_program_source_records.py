"""Owner-recorded program sources are absolute directories in the Programs directory."""
from __future__ import annotations

import json

import pytest

import openprogram.state.paths as paths
from openprogram.programs._host import sources as program_sources


def test_workflow_records_are_dropped(tmp_path, monkeypatch):
    state = tmp_path / 'state'
    state.mkdir()
    monkeypatch.setattr(paths, 'get_state_dir', lambda: state)
    (state / 'program-sources.json').write_text(json.dumps({
        'version': 2, 'workflow_projects_migrated': True,
        'programs': [{'scope': 'programs', 'path': 'workflow/demo', 'kind': 'workflow-publish'}],
    }))
    assert program_sources._read_program_sources() == []


@pytest.mark.parametrize('relative', ['applications/some_harness', 'packages/demo', '../outside', 'relative/path'])
def test_catalog_relative_records_name_nothing(tmp_path, monkeypatch, relative):
    state = tmp_path / 'state'
    (state / 'programs').mkdir(parents=True)
    monkeypatch.setattr(paths, 'get_state_dir', lambda: state)
    (state / 'program-sources.json').write_text(json.dumps({
        'version': 2, 'programs': [{'scope': 'programs', 'path': relative},
                                   {'path': relative}],
    }))
    assert program_sources.owner_controlled_program_sources() == []


def test_a_recorded_clone_records_its_absolute_path(tmp_path, monkeypatch):
    state = tmp_path / 'state'
    harness = state / 'programs' / 'demo_harness'
    harness.mkdir(parents=True)
    monkeypatch.setattr(paths, 'get_state_dir', lambda: state)
    program_sources.record_program_source(harness, source='fixture')
    saved = json.loads((state / 'program-sources.json').read_text())['programs'][0]
    assert saved['path'] == str(harness)
    assert 'scope' not in saved
    assert [row['path'] for row in program_sources.owner_controlled_program_sources()] == [str(harness)]
    program_sources.remove_program_source(harness)
    assert program_sources.owner_controlled_program_sources() == []


def test_removal_revokes_a_missing_source(tmp_path, monkeypatch):
    state = tmp_path / 'state'
    harness = state / 'programs' / 'demo'
    harness.mkdir(parents=True)
    monkeypatch.setattr(paths, 'get_state_dir', lambda: state)
    program_sources.record_program_source(harness, source='fixture')
    harness.rmdir()
    program_sources.remove_program_source(harness)
    harness.mkdir()
    assert program_sources.owner_controlled_program_sources() == []

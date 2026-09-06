from __future__ import annotations

import json

import pytest

import openprogram
import openprogram.paths as paths
from openprogram.programs import _programs


def test_recorded_workflow_survives_checkout_relocation(tmp_path, monkeypatch):
    state = tmp_path / 'state'
    checkout = tmp_path / 'original'
    package = checkout / 'openprogram'
    workflow = package / 'programs' / 'workflow' / 'weekly_report'
    workflow.mkdir(parents=True)
    monkeypatch.setattr(paths, 'get_state_dir', lambda: state)
    monkeypatch.setattr(openprogram, '__file__', str(package / '__init__.py'))
    _programs.record_program_source(workflow, source='workflow:weekly_report',
                                    kind='workflow-publish', base=str(workflow.parent))
    saved = json.loads((state / 'program-sources.json').read_text())['programs'][0]
    assert saved['scope'] == 'programs'
    assert saved['path'] == 'workflow/weekly_report'

    moved = tmp_path / 'moved'
    checkout.rename(moved)
    monkeypatch.setattr(openprogram, '__file__', str(moved / 'openprogram' / '__init__.py'))
    relocated = moved / 'openprogram' / 'programs' / 'workflow' / 'weekly_report'
    assert [row['path'] for row in _programs.owner_controlled_program_sources(str(relocated.parent))] == [str(relocated)]
    _programs.remove_program_source(relocated)
    assert _programs.owner_controlled_program_sources(str(relocated.parent)) == []


@pytest.mark.parametrize('relative', ['../outside', '/tmp/outside', 'workflow/../../outside', 'workflow\\outside'])
def test_scoped_source_rejects_noncanonical_paths(tmp_path, monkeypatch, relative):
    state = tmp_path / 'state'
    state.mkdir()
    monkeypatch.setattr(paths, 'get_state_dir', lambda: state)
    (state / 'program-sources.json').write_text(json.dumps({
        'version': 2, 'programs': [{'scope': 'programs', 'path': relative}],
    }))
    assert _programs.owner_controlled_program_sources() == []


def test_scoped_source_rejects_symlinked_category(tmp_path, monkeypatch):
    state = tmp_path / 'state'
    state.mkdir()
    package = tmp_path / 'openprogram'
    programs = package / 'programs'
    programs.mkdir(parents=True)
    outside = tmp_path / 'outside'
    (outside / 'demo').mkdir(parents=True)
    (programs / 'workflow').symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(paths, 'get_state_dir', lambda: state)
    monkeypatch.setattr(openprogram, '__file__', str(package / '__init__.py'))
    (state / 'program-sources.json').write_text(json.dumps({
        'version': 2, 'programs': [{'scope': 'programs', 'path': 'workflow/demo'}],
    }))
    assert _programs.owner_controlled_program_sources() == []


def test_installed_runtime_uses_explicit_catalog_binding(tmp_path, monkeypatch):
    state = tmp_path / 'state'
    source = tmp_path / 'source' / 'openprogram' / 'programs'
    workflow = source / 'workflow' / 'demo'
    workflow.mkdir(parents=True)
    (source / '__init__.py').write_text('')
    monkeypatch.setattr(paths, 'get_state_dir', lambda: state)
    monkeypatch.setattr(openprogram, '__file__', str(tmp_path / 'installed' / 'openprogram' / '__init__.py'))
    _programs.bind_program_catalog(source)
    _programs.record_program_source(workflow, source='workflow:demo',
                                    kind='workflow-publish', base=str(workflow.parent))
    saved = json.loads((state / 'program-sources.json').read_text())
    assert saved['programs'][0]['path'] == 'workflow/demo'
    assert _programs.owner_programs_roots() == [source]
    assert _programs.owner_controlled_program_sources()[0]['path'] == str(workflow)
    moved = source.parent.parent.with_name('moved')
    source.parent.parent.rename(moved)
    _programs.bind_program_catalog(moved / 'openprogram' / 'programs')
    assert _programs.owner_controlled_program_sources()[0]['path'] == str(moved / 'openprogram' / 'programs' / 'workflow' / 'demo')


def test_removal_revokes_a_missing_portable_source(tmp_path, monkeypatch):
    state = tmp_path / 'state'
    package = tmp_path / 'openprogram'
    workflow = package / 'programs' / 'workflow' / 'demo'
    workflow.mkdir(parents=True)
    monkeypatch.setattr(paths, 'get_state_dir', lambda: state)
    monkeypatch.setattr(openprogram, '__file__', str(package / '__init__.py'))
    _programs.record_program_source(workflow, source='workflow:demo',
                                    kind='workflow-publish', base=str(workflow.parent))
    workflow.rmdir()
    _programs.remove_program_source(workflow)
    workflow.mkdir()
    assert _programs.owner_controlled_program_sources() == []

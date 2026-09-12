"""Bounded report serialization and isolated artifact writes."""
import json

import pytest

from openprogram.programs.workflow.report_io import decode_object, save_report


def test_decode_requires_bounded_object():
    assert decode_object('{"task":"inspect"}') == {"task": "inspect"}
    for value in ('[]', '{', 'x' * 300001):
        with pytest.raises(ValueError):
            decode_object(value)


def test_drafts_never_overwrite_and_manifest_is_last(tmp_path, monkeypatch):
    monkeypatch.setattr('openprogram.programs.workflow.report_io.write_file', lambda path, content: _write(path, content))
    first = save_report('2026-W37', str(tmp_path), 'report', '', {}, [])
    second = save_report('2026-W37', str(tmp_path), 'revised', '', {}, [])
    assert first != second
    assert (first / 'summary.md').read_text() == 'report'
    assert json.loads((second / 'coverage.json').read_text()) == {}


def _write(path, content):
    from pathlib import Path
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(content)
    return 'Wrote bytes'


def test_write_failure_does_not_claim_success(tmp_path, monkeypatch):
    monkeypatch.setattr('openprogram.programs.workflow.report_io.write_file', lambda *args: 'Error: denied')
    with pytest.raises(OSError, match='denied'):
        save_report('2026-W37', str(tmp_path), 'report', '', {}, [])


def test_default_directory_uses_real_file_tool(tmp_path, monkeypatch):
    from openprogram.worktree.context import reset_worktree, set_worktree
    monkeypatch.chdir(tmp_path)
    token = set_worktree(None)
    try:
        result = save_report('2026-W37', '', 'actual draft', '', {}, [])
        assert result.is_absolute()
        assert result.is_relative_to(tmp_path / 'reports/group-weekly/2026-W37')
        assert (result / 'summary.md').read_text() == 'actual draft'
    finally:
        reset_worktree(token)


def test_invalidated_checkpoint_cannot_resume_saved_progress(tmp_path):
    from openprogram.programs.workflow.report_io import load_checkpoint
    import json
    import pytest
    path = tmp_path/'run.json'
    original = json.dumps({'kind':'tencent_delivery','summary':'last week'})
    path.write_text(original)
    path.with_name(path.name+'.invalidated.json').write_text(json.dumps({'reason':'User identified last-week evidence'}))
    with pytest.raises(ValueError, match='Checkpoint invalidated'):
        load_checkpoint(str(path))
    assert path.read_text() == original

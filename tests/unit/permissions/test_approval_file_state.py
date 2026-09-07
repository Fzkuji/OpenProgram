from pathlib import Path

import pytest

from openprogram.agent.permissions import file_state


def test_deleted_approved_file_is_not_recreated(tmp_path, monkeypatch):
    path = tmp_path / 'file.txt'
    path.write_text('before')
    state = file_state.capture('write', {'file_path': str(path)})
    monkeypatch.setattr(file_state, 'current_files', lambda: state)
    path.unlink()
    with pytest.raises(ValueError, match='no longer exists'):
        file_state.write_checked(str(path), 'after')
    assert not path.exists()


def test_changed_file_is_not_overwritten(tmp_path, monkeypatch):
    path = tmp_path / 'file.txt'
    path.write_text('before')
    state = file_state.capture('edit', {'file_path': str(path)})
    monkeypatch.setattr(file_state, 'current_files', lambda: state)
    path.write_text('user changes')
    with pytest.raises(ValueError, match='File state changed'):
        file_state.write_checked(str(path), 'after')
    assert path.read_text() == 'user changes'


def test_change_between_check_and_open_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / 'file.txt'
    path.write_text('before')
    state = file_state.capture('edit', {'file_path': str(path)})
    monkeypatch.setattr(file_state, 'current_files', lambda: state)
    original = file_state.os.open
    def changing_open(*args):
        path.write_text('concurrent change')
        return original(*args)
    monkeypatch.setattr(file_state.os, 'open', changing_open)
    with pytest.raises(ValueError, match='File changed before writing'):
        file_state.write_checked(str(path), 'after')
    assert path.read_text() == 'concurrent change'


def test_unchanged_file_can_be_written(tmp_path, monkeypatch):
    path = tmp_path / 'file.txt'
    path.write_text('before')
    state = file_state.capture('edit', {'file_path': str(path)})
    monkeypatch.setattr(file_state, 'current_files', lambda: state)
    file_state.write_checked(str(path), 'after')
    assert path.read_text() == 'after'

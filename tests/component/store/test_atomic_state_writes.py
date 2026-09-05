"""Private profile-state publication and failure cleanup."""
import os
import stat
from types import SimpleNamespace

import pytest

from openprogram.store.session import git_session


def test_atomic_state_is_private_before_and_after_publication(tmp_path, monkeypatch):
    target = tmp_path / 'state.json'
    original = git_session.os.replace
    modes = []

    def replace(source, destination):
        modes.append(stat.S_IMODE(os.stat(source).st_mode))
        return original(source, destination)

    monkeypatch.setattr(git_session.os, 'replace', replace)
    git_session.atomic_write_text(target, 'private state')
    assert target.read_text() == 'private state'
    if os.name != 'nt':
        assert modes == [0o600]
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert list(tmp_path.iterdir()) == [target]


def test_atomic_state_failure_preserves_old_value_and_cleans_own_temp(tmp_path, monkeypatch):
    target = tmp_path / 'state.json'
    target.write_text('old state')

    def fail(*_args):
        raise OSError('replace failed')

    monkeypatch.setattr(git_session.os, 'replace', fail)
    with pytest.raises(OSError, match='replace failed'):
        git_session.atomic_write_text(target, 'new state')
    assert target.read_text() == 'old state'
    assert list(tmp_path.iterdir()) == [target]


def test_atomic_state_does_not_overwrite_or_remove_an_existing_temp(tmp_path, monkeypatch):
    target = tmp_path / 'state.json'
    collision = tmp_path / 'state.json.fixed.tmp'
    collision.write_text('other writer')
    monkeypatch.setattr(git_session.uuid, 'uuid4', lambda: SimpleNamespace(hex='fixed'))
    with pytest.raises(FileExistsError):
        git_session.atomic_write_text(target, 'new state')
    assert collision.read_text() == 'other writer'
    assert not target.exists()

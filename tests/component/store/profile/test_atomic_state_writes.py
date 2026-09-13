"""Private profile-state publication and failure cleanup."""
import os
from pathlib import Path
import stat
import threading
from types import SimpleNamespace

import pytest

from openprogram.store.session import git_session


def test_atomic_state_is_private_before_and_after_publication(tmp_path, monkeypatch):
    target = tmp_path / 'state.json'
    original = git_session.os.replace
    modes = []

    def replace(source, destination):
        if Path(destination) == target:
            modes.append(stat.S_IMODE(os.stat(source).st_mode))
        return original(source, destination)

    monkeypatch.setattr(git_session.os, 'replace', replace)
    git_session.atomic_write_text(target, 'private state')
    assert target.read_text() == 'private state'
    if os.name != 'nt':
        assert modes == [0o600]
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert list(tmp_path.iterdir()) == [target]
    # Other stores may flush while this process-global OS hook is installed.
    # Their replacements must not count as publications by this test.
    git_session.atomic_write_text(tmp_path / 'other.json', 'unrelated writer')
    assert len(modes) == 1


def test_atomic_state_failure_preserves_old_value_and_cleans_own_temp(tmp_path, monkeypatch):
    target = tmp_path / 'state.json'
    target.write_text('old state')

    original = git_session.os.replace

    def fail(source, destination):
        if Path(destination) == target:
            raise OSError('replace failed')
        return original(source, destination)

    monkeypatch.setattr(git_session.os, 'replace', fail)
    with pytest.raises(OSError, match='replace failed'):
        git_session.atomic_write_text(target, 'new state')
    assert target.read_text() == 'old state'
    assert list(tmp_path.iterdir()) == [target]
    other = tmp_path / 'other.json'
    git_session.atomic_write_text(other, 'unrelated writer')
    assert other.read_text() == 'unrelated writer'


def test_atomic_state_does_not_overwrite_or_remove_an_existing_temp(tmp_path, monkeypatch):
    target = tmp_path / 'state.json'
    collision = tmp_path / 'state.json.fixed.tmp'
    collision.write_text('other writer')
    original_uuid4 = git_session.uuid.uuid4
    owner = threading.get_ident()
    monkeypatch.setattr(git_session, 'uuid', SimpleNamespace(
        uuid4=lambda: SimpleNamespace(hex='fixed') if threading.get_ident() == owner else original_uuid4(),
    ))
    with pytest.raises(FileExistsError):
        git_session.atomic_write_text(target, 'new state')
    assert collision.read_text() == 'other writer'
    assert not target.exists()

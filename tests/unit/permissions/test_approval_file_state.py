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


def test_approved_write_without_posix_open_flags(tmp_path, monkeypatch):
    path = tmp_path / 'file.txt'
    path.write_bytes(b'before')
    state = file_state.capture('write', {'file_path': str(path)})
    monkeypatch.setattr(file_state, 'current_files', lambda: state)
    monkeypatch.delattr(file_state.os, 'O_NOFOLLOW', raising=False)
    file_state.write_checked(str(path), 'after\n中文\x1a')
    assert path.read_bytes() == 'after\n中文\x1a'.encode()


def test_missing_approved_file_can_be_created(tmp_path, monkeypatch):
    path = tmp_path / 'new.txt'
    state = file_state.capture('write', {'file_path': str(path)})
    monkeypatch.setattr(file_state, 'current_files', lambda: state)
    file_state.write_checked(str(path), 'new\ncontent')
    assert path.read_bytes() == b'new\ncontent'


def test_approved_symlink_is_rejected_without_posix_open_flags(tmp_path, monkeypatch):
    target = tmp_path / 'target.txt'
    target.write_text('before')
    path = tmp_path / 'link.txt'
    try:
        path.symlink_to(target)
    except OSError:
        pytest.skip('host does not permit symlink creation')
    state = file_state.capture('write', {'file_path': str(path)})
    monkeypatch.setattr(file_state, 'current_files', lambda: state)
    monkeypatch.delattr(file_state.os, 'O_NOFOLLOW', raising=False)
    with pytest.raises(ValueError, match='regular file'):
        file_state.write_checked(str(path), 'after')
    assert target.read_text() == 'before'


def test_approved_write_does_not_reopen_locked_file(tmp_path, monkeypatch):
    import builtins
    path = tmp_path / 'file.txt'
    path.write_bytes(b'before')
    state = file_state.capture('write', {'file_path': str(path)})
    monkeypatch.setattr(file_state, 'current_files', lambda: state)
    real_open = builtins.open
    locked = []
    real_lock = file_state.flock
    def lock(fd, mode):
        real_lock(fd, mode)
        locked.append(fd)
    def guarded_open(name, *args, **kwargs):
        if str(name) == str(path) and locked:
            try:
                file_state.os.fstat(locked[0])
            except OSError:
                pass  # The locked descriptor was closed.
            else:
                raise PermissionError('Windows rejects reads through another handle while locked')
        return real_open(name, *args, **kwargs)
    monkeypatch.setattr(file_state, 'flock', lock)
    monkeypatch.setattr(builtins, 'open', guarded_open)
    file_state.write_checked(str(path), 'after')
    assert path.read_text() == 'after'


@pytest.mark.skipif(file_state.os.name == 'nt', reason='Windows disallows renaming this open file')
def test_path_replaced_during_locked_hash_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / 'file.txt'
    moved = tmp_path / 'moved.txt'
    path.write_text('before')
    state = file_state.capture('write', {'file_path': str(path)})
    monkeypatch.setattr(file_state, 'current_files', lambda: state)
    original = file_state.hashlib.file_digest
    calls = []
    def replacing_digest(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(1)
        if len(calls) == 2:
            path.rename(moved)
            path.write_text('replacement')
        return result
    monkeypatch.setattr(file_state.hashlib, 'file_digest', replacing_digest)
    with pytest.raises(ValueError, match='regular file'):
        file_state.write_checked(str(path), 'after')
    assert moved.read_text() == 'before'
    assert path.read_text() == 'replacement'


@pytest.mark.parametrize('changed', ['target', 'source'])
def test_binary_publication_rechecks_files_after_staging(tmp_path, monkeypatch, changed):
    target, source = tmp_path / 'target.docx', tmp_path / 'source.docx'
    target.write_bytes(b'before')
    source.write_bytes(b'after')
    state = file_state.fingerprint(str(target))
    source_state = {str(source): file_state.fingerprint(str(source))}
    monkeypatch.setattr(file_state, 'current_files', lambda: {})
    real_sync = file_state.os.fsync

    def concurrent_change(fd):
        real_sync(fd)
        (target if changed == 'target' else source).write_bytes(b'external change')

    monkeypatch.setattr(file_state.os, 'fsync', concurrent_change)
    with pytest.raises(ValueError, match='File state changed'):
        file_state.write_checked_atomic(str(target), b'after', expected_state=state,
                                        source_state=source_state)
    assert target.read_bytes() == (b'external change' if changed == 'target' else b'before')
    assert not list(tmp_path.glob('.openprogram-write-*'))


def test_binary_source_is_part_of_approval(tmp_path, monkeypatch):
    target, source = tmp_path / 'target.docx', tmp_path / 'source.docx'
    target.write_bytes(b'before')
    source.write_bytes(b'approved')
    approved = file_state.capture('write', {'file_path': str(target), 'source_path': str(source)})
    monkeypatch.setattr(file_state, 'current_files', lambda: approved)
    source.write_bytes(b'changed after approval')
    with pytest.raises(ValueError, match='File state changed'):
        file_state.write_checked_atomic(
            str(target), source.read_bytes(), expected_state=file_state.fingerprint(str(target)),
            source_state={str(source): file_state.fingerprint(str(source))},
        )
    assert target.read_bytes() == b'before'


def test_binary_publish_failure_preserves_original_and_cleans_staging(tmp_path, monkeypatch):
    target = tmp_path / 'target.docx'
    target.write_bytes(b'before')
    monkeypatch.setattr(file_state, 'current_files', lambda: {})

    def deny_replace(*args):
        raise PermissionError('file in use')

    monkeypatch.setattr(file_state.os, 'replace', deny_replace)
    with pytest.raises(PermissionError, match='file in use'):
        file_state.write_checked_atomic(str(target), b'after',
                                        expected_state=file_state.fingerprint(str(target)),
                                        source_state={})
    assert target.read_bytes() == b'before'
    assert not list(tmp_path.glob('.openprogram-write-*'))


@pytest.mark.skipif(file_state.os.name == 'nt', reason='POSIX permission bits')
def test_binary_publication_preserves_target_permissions(tmp_path, monkeypatch):
    target = tmp_path / 'target.docx'
    target.write_bytes(b'before')
    target.chmod(0o640)
    monkeypatch.setattr(file_state, 'current_files', lambda: {})
    file_state.write_checked_atomic(str(target), b'after',
                                    expected_state=file_state.fingerprint(str(target)),
                                    source_state={})
    assert target.read_bytes() == b'after'
    assert target.stat().st_mode & 0o777 == 0o640

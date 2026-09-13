"""Archive and target validation before restore publication."""
from __future__ import annotations
import io
import json
import os
import tarfile
from pathlib import Path
import pytest

from tests.integration.store.backup.restore_support import (
    _state,
    _archive,
)


def test_restore_uses_descriptor_operations_when_available() -> None:
    from openprogram.cli.commands.backup import _RESTORE_DIR_FD_CAPABLE

    expected = all(
        function in os.supports_dir_fd
        for function in (os.open, os.stat, os.unlink, os.rename)
    )
    assert _RESTORE_DIR_FD_CAPABLE is expected


def test_restore_rejects_traversal_member_without_publishing(tmp_path: Path) -> None:
    from openprogram.cli.commands.backup import restore_archive

    state = _state(tmp_path)
    (state / "config.json").write_text('{"keep": true}')
    archive = _archive(tmp_path, {"../escape.json": b"{}", "config.json": b"{}"})

    with pytest.raises(tarfile.TarError):
        restore_archive(archive, state)

    assert json.loads((state / "config.json").read_text()) == {"keep": True}
    assert not (tmp_path / "escape.json").exists()


def test_restore_rejects_symlink_member(tmp_path: Path) -> None:
    from openprogram.cli.commands.backup import _MANIFEST_NAME, restore_archive

    state = _state(tmp_path)
    (state / "config.json").write_text('{"keep": true}')
    archive = tmp_path / "evil.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        link = tarfile.TarInfo("config.json")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        tar.addfile(link)
        body = json.dumps({"format_version": 1}).encode()
        info = tarfile.TarInfo(_MANIFEST_NAME)
        info.size = len(body)
        tar.addfile(info, io.BytesIO(body))

    with pytest.raises(tarfile.TarError):
        restore_archive(archive, state)

    assert json.loads((state / "config.json").read_text()) == {"keep": True}
    assert not (state / "config.json").is_symlink()


def test_restore_rejects_hardlink_member(tmp_path: Path) -> None:
    from openprogram.cli.commands.backup import _MANIFEST_NAME, restore_archive

    state = _state(tmp_path)
    archive = tmp_path / "evil.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        payload = b"{}"
        info = tarfile.TarInfo("config.json")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
        link = tarfile.TarInfo("auth/openai/default.json")
        link.type = tarfile.LNKTYPE
        link.linkname = "config.json"
        tar.addfile(link)
        body = json.dumps({"format_version": 1}).encode()
        manifest = tarfile.TarInfo(_MANIFEST_NAME)
        manifest.size = len(body)
        tar.addfile(manifest, io.BytesIO(body))

    with pytest.raises(tarfile.TarError):
        restore_archive(archive, state)


def test_restore_rejects_duplicate_member_names(tmp_path: Path) -> None:
    from openprogram.cli.commands.backup import _MANIFEST_NAME, restore_archive

    state = _state(tmp_path)
    archive = tmp_path / "duplicate.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for payload in (b'{"value": 1}', b'{"value": 2}'):
            info = tarfile.TarInfo("config.json")
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
        body = json.dumps(
            {"format_version": 1, "credential_opt_in": False}
        ).encode()
        manifest = tarfile.TarInfo(_MANIFEST_NAME)
        manifest.size = len(body)
        tar.addfile(manifest, io.BytesIO(body))

    with pytest.raises(tarfile.TarError, match="duplicate"):
        restore_archive(archive, state)

    assert not (state / "config.json").exists()


def test_restore_rejects_a_missing_manifest(tmp_path: Path) -> None:
    from openprogram.cli.commands.backup import restore_archive

    state = _state(tmp_path)
    archive = tmp_path / "bare.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        payload = b"{}"
        info = tarfile.TarInfo("config.json")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))

    with pytest.raises(tarfile.TarError):
        restore_archive(archive, state)


@pytest.mark.parametrize(
    "manifest",
    [
        [],
        {"format_version": 2, "credential_opt_in": False},
        {"format_version": True, "credential_opt_in": False},
        {"format_version": 1, "credential_opt_in": "false"},
    ],
)
def test_restore_rejects_invalid_manifest_schema(tmp_path: Path, manifest) -> None:
    from openprogram.cli.commands.backup import restore_archive

    state = _state(tmp_path)
    archive = _archive(
        tmp_path, {"config.json": b"{}"}, manifest=json.dumps(manifest).encode()
    )

    with pytest.raises(tarfile.TarError, match="manifest"):
        restore_archive(archive, state)

    assert not (state / "config.json").exists()


def test_restore_rejects_a_registered_secret_member_that_is_not_json(
    tmp_path: Path,
) -> None:
    from openprogram.cli.commands.backup import restore_archive

    state = _state(tmp_path)
    (state / "config.json").write_text('{"keep": true}')
    archive = _archive(tmp_path, {"config.json": b"this is not json"})

    with pytest.raises(tarfile.TarError):
        restore_archive(archive, state)

    assert json.loads((state / "config.json").read_text()) == {"keep": True}


def test_restore_rejects_secret_members_when_manifest_denies_opt_in(
    tmp_path: Path,
) -> None:
    from openprogram.cli.commands.backup import restore_archive

    state = _state(tmp_path)
    archive = _archive(
        tmp_path,
        {"auth/openai/default.json": b'{"credentials": []}'},
        manifest=json.dumps(
            {"format_version": 1, "credential_opt_in": False}
        ).encode(),
    )

    with pytest.raises(tarfile.TarError, match="credential_opt_in"):
        restore_archive(archive, state)

    assert not (state / "auth").exists()


def test_restore_rejects_unknown_credential_tree_member_without_publishing(
    tmp_path: Path,
) -> None:
    from openprogram.cli.commands.backup import restore_archive

    state = _state(tmp_path)
    (state / "config.json").write_text('{"keep": true}')
    (state / "config.json").chmod(0o600)
    archive = _archive(
        tmp_path,
        {
            "config.json": b'{"keep": false}',
            "auth/openai/unknown.txt": b"secret",
        },
        manifest=json.dumps(
            {"format_version": 1, "credential_opt_in": True}
        ).encode(),
    )

    with pytest.raises(tarfile.TarError, match="credential inventory"):
        restore_archive(archive, state)

    assert json.loads((state / "config.json").read_text()) == {"keep": True}
    assert not (state / "auth").exists()


def test_restored_secret_files_are_published(tmp_path: Path) -> None:
    from openprogram.cli.commands.backup import restore_archive

    state = _state(tmp_path)
    archive = _archive(
        tmp_path,
        {
            "config.json": b'{"api_keys": {}}',
            "auth/openai/default.json": b'{"credentials": []}',
        },
        manifest=json.dumps(
            {"format_version": 1, "credential_opt_in": True}
        ).encode(),
    )

    restore_archive(archive, state)

    assert (state / "config.json").read_bytes() == b'{"api_keys": {}}'
    assert (state / "auth/openai/default.json").read_bytes() == (
        b'{"credentials": []}'
    )


def test_restore_preserves_local_secrets_for_redacted_fields(tmp_path: Path) -> None:
    from openprogram.cli.commands.backup import restore_archive

    state = _state(tmp_path)
    (state / "config.json").write_text(
        json.dumps({"api_keys": {"OPENAI_API_KEY": "sk-local"}, "ui": {"port": 1}})
    )
    os.chmod(state / "config.json", 0o600)
    # A default archive carries config.json with api_keys redacted away.
    archive = _archive(tmp_path, {"config.json": json.dumps({"ui": {"port": 2}}).encode()})

    restore_archive(archive, state)

    restored = json.loads((state / "config.json").read_text())
    assert restored["api_keys"] == {"OPENAI_API_KEY": "sk-local"}
    assert restored["ui"] == {"port": 2}


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_restore_rejects_existing_target_symlink_without_changing_its_type(
    tmp_path: Path,
) -> None:
    from openprogram.cli.commands.backup import restore_archive

    state = _state(tmp_path)
    real = state / "real.json"
    real.write_text('{"keep": true}')
    target = state / "config.json"
    target.symlink_to(real)
    archive = _archive(tmp_path, {"config.json": b"{}"})

    with pytest.raises(OSError):
        restore_archive(archive, state)

    assert target.is_symlink()
    assert json.loads(real.read_text()) == {"keep": True}

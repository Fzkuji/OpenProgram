"""Signing subprocess failures cannot publish private keychain arguments."""
import subprocess

import pytest

from openprogram.self_update import local_signing


@pytest.mark.parametrize("failure", ["timeout", "exit"])
def test_imported_signer_redacts_private_arguments(monkeypatch, failure):
    secret = "fixture-private-keychain-password"

    def fail(args, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, 60, stderr=secret)
        return subprocess.CompletedProcess(args, 1, "", secret)

    monkeypatch.setattr(local_signing.subprocess, "run", fail)
    with pytest.raises(RuntimeError) as error:
        local_signing.run("/usr/bin/security", "unlock-keychain", "-p", secret, "/tmp/keychain")
    assert str(error.value) == "local signing failed: security unlock-keychain"
    assert secret not in str(error.value)
    if failure == "timeout":
        assert error.value.__suppress_context__

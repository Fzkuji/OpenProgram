"""Which versions a switch committed, read from a real bare repository."""
import os
import subprocess

from openprogram.operations.self_update.versions import store
from tests.support.sealed_versions import make_layout


def test_committed_versions_come_from_pinning_refs(tmp_path):
    layout = make_layout(tmp_path / "state" / "runtime")
    env = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "PATH": os.environ["PATH"]}
    subprocess.run(["git", "init", "-q", "--bare", str(layout.repo)], check=True, env=env)
    tree = subprocess.run(["git", "-C", str(layout.repo), "hash-object", "-t", "tree", "-w", "--stdin"],
                          input=b"", capture_output=True, check=True, env=env).stdout.decode().strip()
    commit = subprocess.run(
        ["git", "-C", str(layout.repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit-tree", tree, "-m", "m"],
        capture_output=True, check=True, env=env).stdout.decode().strip()
    subprocess.run(["git", "-C", str(layout.repo), "update-ref", "refs/openprogram/version/v9", commit], check=True, env=env)
    assert store.committed_versions(layout) == {"v9"}

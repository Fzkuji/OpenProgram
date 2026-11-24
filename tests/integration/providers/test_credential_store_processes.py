"""Several processes writing one provider's credential list
(openprogram/models/auth/store.py): a change holds the file's lock from its
read to its write, and a request's use is counted outside the secrets file."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from openprogram.models.auth.store import AuthStore
from openprogram.models.auth.types import Credential, CredentialData

_SLOW_RENAME = """
import sys, time
from pathlib import Path
from openprogram.models.auth.store import AuthStore
root, cid, ready = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
def rename(row):
    ready.write_text("1")
    time.sleep(1.0)  # the other process writes meanwhile
    row.label = "renamed"
AuthStore(root=root).update("openai", cid, rename)
"""

_SPEND = """
import sys
from pathlib import Path
from openprogram.models.auth.store import AuthStore
store = AuthStore(root=Path(sys.argv[1]))
for i in range(25):
    store.record_use("openai", sys.argv[2], 1000 + i)
"""


def _key(value: str) -> Credential:
    return Credential(provider_id="openai", kind="api_key", source="cli",
                      payload=CredentialData(kind="api_key", auth_value=value))


def _python(script: str, *args: str) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", script, *args])


def test_a_key_added_while_another_process_changes_the_list_is_kept(tmp_path: Path):
    store = AuthStore(root=tmp_path)
    first = store.add_credential(_key("sk-first-000000001"))
    ready = tmp_path / "ready"
    worker = _python(_SLOW_RENAME, str(tmp_path), first.id, str(ready))
    deadline = time.time() + 30
    while not ready.exists() and time.time() < deadline:
        time.sleep(0.02)
    assert ready.exists()
    # The CLI adds a key while the worker is between its read and its write.
    AuthStore(root=tmp_path).add_credential(_key("sk-second-00000002"))
    assert worker.wait(30) == 0
    rows = AuthStore(root=tmp_path).get("openai").credentials
    assert [r.payload.auth_value for r in rows] == ["sk-first-000000001", "sk-second-00000002"]
    assert rows[0].label == "renamed"
    # The way's file keeps both rows and neither secret.
    saved = json.loads((tmp_path / "auth" / "openai" / "credentials.json").read_text())["credentials"]
    assert [r["payload"]["auth_value"] for r in saved] == ["", ""]


def test_uses_counted_by_four_processes_all_arrive_and_leave_the_secrets_file_alone(tmp_path: Path):
    cred = AuthStore(root=tmp_path).add_credential(_key("sk-count-00000001"))
    secrets_file = tmp_path / "auth" / "openai" / "credentials.json"
    before = secrets_file.read_bytes()
    workers = [_python(_SPEND, str(tmp_path), cred.id) for _ in range(4)]
    assert [w.wait(60) for w in workers] == [0, 0, 0, 0]
    assert AuthStore(root=tmp_path).find("openai", cred.id).use_count == 100
    assert secrets_file.read_bytes() == before

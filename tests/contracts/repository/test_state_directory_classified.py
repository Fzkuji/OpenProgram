"""Every top-level name the code writes under the state directory has a backup decision."""
from __future__ import annotations

from pathlib import Path

from openprogram.operations import backup
from scripts.docs_site.governance import state_names

ROOT = Path(__file__).resolve().parents[3]


def test_every_state_name_written_in_code_is_classified() -> None:
    known = set(backup.INCLUDED) | set(backup.CREDENTIAL_ENTRIES) | set(backup.REGENERATED)
    missing = {name: sorted(files)[:2] for name, files in state_names.scan(ROOT).items() if name not in known}
    assert not missing, f"add these to INCLUDED, CREDENTIAL_ENTRIES or REGENERATED in backup.py: {missing}"


def test_a_name_has_one_decision() -> None:
    assert not set(backup.INCLUDED) & set(backup.REGENERATED)
    assert not set(backup.CREDENTIAL_ENTRIES) & (set(backup.INCLUDED) | set(backup.REGENERATED))


def test_the_scan_sees_the_ways_code_builds_a_state_path() -> None:
    source = '''
from pathlib import Path
NAME = "constant-name"
def helper():
    return Path(get_state_dir())
a = get_state_dir() / "direct"
b = helper() / "through-helper" / "deeper"
d = get_state_dir()
c = d / NAME
e = os.path.join(d, "joined")
'''
    assert state_names.names_in_source(source) == {"direct", "through-helper", "constant-name", "joined"}

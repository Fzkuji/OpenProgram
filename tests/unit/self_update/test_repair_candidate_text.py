"""Source repair matches model text while preserving source line endings."""
from types import SimpleNamespace

import pytest

from openprogram.self_update.repair_candidate import _edits
from openprogram.self_update.types import IterationMode


def _request():
    return SimpleNamespace(
        iteration_policy=SimpleNamespace(mode=IterationMode.APPROVE_EACH_ACTIVATION),
        changed_paths=("feature.txt",),
    )


@pytest.mark.parametrize("ending", ["\n", "\r\n"])
def test_repair_preserves_source_newlines(tmp_path, ending):
    root = tmp_path.resolve()
    path = root / "feature.txt"
    original = f"first{ending}candidate{ending}last{ending}"
    path.write_bytes(original.encode())
    changed = _edits(_request(), root, [
        dict(path="feature.txt", old_text="candidate\n", new_text="repaired\nextra\n"),
    ])
    assert changed[path] == (original, f"first{ending}repaired{ending}extra{ending}last{ending}")
    assert path.read_bytes() == original.encode()


def test_repair_rejects_ambiguous_normalized_match(tmp_path):
    root = tmp_path.resolve()
    (root / "feature.txt").write_bytes(b"candidate\r\ncandidate\r\n")
    with pytest.raises(ValueError, match="uniquely match"):
        _edits(_request(), root, [
            dict(path="feature.txt", old_text="candidate\n", new_text="repaired\n"),
        ])


def test_repair_allows_whole_crlf_file_deletion(tmp_path):
    root = tmp_path.resolve()
    path = root / "feature.txt"
    path.write_bytes(b"candidate\r\n")
    assert _edits(_request(), root, [
        dict(path="feature.txt", old_text="candidate\n", new_text=None),
    ])[path] == ("candidate\r\n", None)

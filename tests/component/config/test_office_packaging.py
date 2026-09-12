from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
PREPARE = ROOT / "scripts/release/office/prepare.py"


def test_prepare_public_cli_rejects_unpinned_source_and_preserves_ready_pack(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "-C", str(source), "init", "-q"], check=True)
    (source / "README").write_text("fixture", encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "add", "README"], check=True)
    subprocess.run(
        ["git", "-C", str(source), "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture"],
        check=True,
    )
    output = tmp_path / "office"
    output.mkdir()
    (output / "previous-ready-bytes").write_bytes(b"keep")
    result = subprocess.run(
        ["python3", str(PREPARE), "--source", str(source), "--output", str(output)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "source revision mismatch" in result.stderr
    assert (output / "previous-ready-bytes").read_bytes() == b"keep"


def test_product_manifest_declares_reviewed_office_inputs():
    config = json.loads((ROOT / "scripts/release/product-runtime.json").read_text(encoding="utf-8"))
    office = config["office"]
    assert office["packageVersion"] == "0.3.34"
    assert office["source"] == "d15d12b6945be4d8b0f3aa1806120e740d2950ee"
    assert office["adoptionPatchSha256"] == hashlib.sha256(
        (ROOT / "scripts/release/office/adoption.patch").read_bytes()
    ).hexdigest()
    assert office["reviewedNpmLockSha256"] == hashlib.sha256(
        (ROOT / "scripts/release/office/package-lock.json").read_bytes()
    ).hexdigest()

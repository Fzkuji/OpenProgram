"""A runtime ships one Node: Playwright's driver runs on ``bin/node``."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts" / "release" / "share-runtime-node.py"


def _module():
    spec = importlib.util.spec_from_file_location("share_runtime_node", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _runtime(tmp_path: Path, python: str) -> tuple[Path, Path]:
    runtime = tmp_path / "runtime"
    driver = runtime / "python" / python / "site-packages" / "playwright" / "driver"
    driver.mkdir(parents=True)
    (driver / "node").write_bytes(b"playwright's node")
    (runtime / "bin").mkdir()
    (runtime / "bin" / "node").write_bytes(b"the runtime's node")
    return runtime, driver


@pytest.mark.skipif(sys.platform == "win32", reason="Windows keeps the driver's own Node")
@pytest.mark.parametrize("python", ["lib/python3.12", "cpython-3.12.10-macos-aarch64-none/lib/python3.12"])
def test_the_driver_node_becomes_a_relative_link_that_survives_a_move(tmp_path, python):
    share = _module().share
    runtime, driver = _runtime(tmp_path, python)

    assert share(runtime) == [driver]
    assert not os.path.isabs(os.readlink(driver / "node"))
    assert (driver / "node").read_bytes() == b"the runtime's node"
    assert share(runtime) == [], "a second run changes nothing"

    moved = tmp_path / "moved"
    runtime.rename(moved)
    assert (moved / driver.relative_to(runtime) / "node").read_bytes() == b"the runtime's node"


def test_a_runtime_without_its_own_node_keeps_the_driver_node(tmp_path):
    share = _module().share
    runtime, driver = _runtime(tmp_path, "lib/python3.12")
    (runtime / "bin" / "node").unlink()

    assert share(runtime) == []
    assert (driver / "node").read_bytes() == b"playwright's node"


def test_every_builder_that_installs_playwright_shares_the_node():
    for builder, argument in (
        ("scripts/release/build-base.sh", '"$dest"'),
        ("scripts/release/build-product-runtime.sh", '"$runtime_root"'),
    ):
        lines = (ROOT / builder).read_text(encoding="utf-8").splitlines()
        calls = [index for index, line in enumerate(lines) if "share-runtime-node.py" in line and line.rstrip().endswith(argument)]
        assert len(calls) == 1, builder
        placed = max(index for index, line in enumerate(lines) if "bin/node" in line and index < calls[0])
        assert placed < calls[0], f"{builder} shares the Node only after bin/node is in place"

"""The dummy versions the candidate's updater is proved on."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from openprogram.operations.self_update.trial import engine
from openprogram.operations.self_update.versions.layout import Layout

BASE = "py3.12-216d6c812b107d3c"


def _dummy_root(tmp_path: Path) -> tuple[Layout, Layout]:
    live = Layout(tmp_path / "live")
    live.base(BASE).mkdir(parents=True)
    run = SimpleNamespace(workspace=SimpleNamespace(directory=tmp_path / "trial"), manifest={"base": BASE}, layout=live)
    (tmp_path / "trial").mkdir()
    root, _ = engine._Run._dummy_root(run)
    return live, root


def test_the_macos_launcher_gets_a_real_base_to_load_python_from(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine.sys, "platform", "darwin")

    live, root = _dummy_root(tmp_path)

    assert root.base(BASE).resolve() == live.base(BASE).resolve()
    configuration = (root.version("dummy-b") / "venv" / "pyvenv.cfg").read_text(encoding="utf-8")
    assert configuration.startswith("home = ")

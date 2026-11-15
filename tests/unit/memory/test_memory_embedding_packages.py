"""Semantic recall's packages install on demand beside a runtime without torch."""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from openprogram.state.memory.retrieval import embedding_model, inspect


@pytest.fixture
def state(tmp_path, monkeypatch):
    import openprogram.state.paths as paths

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(sys, "path", list(sys.path))
    return tmp_path / "state"


def _fake_package(root, *names):
    for name in names:
        (root / name).mkdir(parents=True, exist_ok=True)
        (root / name / "__init__.py").write_text("", encoding="utf-8")


def test_installed_packages_go_on_the_path_after_the_runtime(state, monkeypatch):
    target = embedding_model.packages_dir()
    assert target.is_relative_to(state / "extras" / "embedding")
    _fake_package(target, "sentence_transformers", "torch")
    monkeypatch.setattr(
        embedding_model.importlib.util, "find_spec",
        lambda name: SimpleNamespace() if (target / name).is_dir() else None,
    )
    assert embedding_model.packages_available() is True
    assert sys.path[-1] == str(target)


def test_cached_model_files_alone_do_not_make_semantic_recall_available(monkeypatch):
    monkeypatch.setattr(embedding_model, "default_model_is_cached", lambda: True)
    monkeypatch.setattr(embedding_model, "packages_available", lambda: False)
    assert inspect.embedding_is_available() is False
    assert embedding_model.semantic_available() is False


def test_install_stages_with_runtime_pins_then_moves_into_place(state, monkeypatch):
    calls = []
    monkeypatch.setattr(embedding_model, "_runtime_constraints", lambda: ["numpy==2.4.6"])
    available = iter([False, True])
    monkeypatch.setattr(embedding_model, "packages_available", lambda: next(available))

    def run(command, **_kwargs):
        calls.append(command)
        staging = command[command.index("--target") + 1]
        constraints = command[command.index("--constraint") + 1]
        assert open(constraints).read() == "numpy==2.4.6\n"
        _fake_package(embedding_model.Path(staging), "sentence_transformers", "torch")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(embedding_model.subprocess, "run", run)
    embedding_model.install_packages()

    target = embedding_model.packages_dir()
    assert (target / "sentence_transformers" / "__init__.py").is_file()
    assert [p.name for p in target.parent.iterdir()] == [target.name]
    assert calls[0][:5] == [sys.executable, "-I", "-m", "pip", "install"]
    assert calls[0][-2:] == list(embedding_model.PACKAGES)


def test_a_failed_install_leaves_nothing_behind_and_says_why(state, monkeypatch):
    monkeypatch.setattr(embedding_model, "_runtime_constraints", lambda: [])
    monkeypatch.setattr(embedding_model, "packages_available", lambda: False)
    monkeypatch.setattr(
        embedding_model.subprocess, "run",
        lambda *_a, **_k: SimpleNamespace(
            returncode=1, stdout="", stderr="ERROR: ResolutionImpossible",
        ),
    )
    with pytest.raises(RuntimeError, match="ResolutionImpossible"):
        embedding_model.install_packages()
    parent = embedding_model.packages_dir().parent
    assert not parent.exists() or list(parent.iterdir()) == []

"""The user's Codex: how it was installed, what an update brings, and running it (after T3 Code)."""
import os
import subprocess

import pytest

from openprogram.runtime.harnesses.codex import maintenance as m


def _link(tmp_path, real_rel, link_rel="bin/codex", tools=()):
    real = tmp_path / real_rel
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_text("#!/bin/sh\n")
    real.chmod(0o755)
    link = tmp_path / link_rel
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(real)
    for tool in tools:
        (link.parent / tool).write_text("#!/bin/sh\n")
        (link.parent / tool).chmod(0o755)
    return str(link)


def test_npm_global_updates_with_the_npm_beside_it(tmp_path):
    binary = _link(tmp_path, "lib/node_modules/@openai/codex/bin/codex.js", tools=("npm",))
    method = m.install_method(binary)
    assert method["kind"] == "npm"
    assert method["argv"] == [str(tmp_path / "bin/npm"), "install", "-g", "@openai/codex@latest"]
    assert method["env"]["PATH"].startswith(str(tmp_path / "bin"))


def test_homebrew_cask_and_standalone(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "_tool", lambda name, *_: f"/x/{name}")
    cask = _link(tmp_path, "Caskroom/codex/0.1/codex", "a/codex")
    assert m.install_method(cask)["argv"] == ["/x/brew", "upgrade", "--cask", "codex"]
    alone = _link(tmp_path, "home/packages/standalone/0.1/codex", "b/codex")
    method = m.install_method(alone)
    assert method["argv"] == [alone, "update"]
    assert method["env"]["CODEX_HOME"] == str((tmp_path / "home").resolve())


def test_unknown_install_has_no_update(tmp_path):
    assert m.install_method(_link(tmp_path, "somewhere/codex")) is None


def test_update_info_names_the_models_a_newer_codex_lists(monkeypatch):
    monkeypatch.setattr(m, "install_method", lambda _b: {"kind": "npm", "argv": [], "env": {}})
    monkeypatch.setattr(m, "latest_version", lambda _k: "0.172.0")
    monkeypatch.setattr(m, "manifest_models", lambda: [
        {"slug": "gpt-new", "name": "GPT New"}, {"slug": "gpt-old", "name": "GPT Old"},
        {"slug": "gpt-mid", "name": "GPT Mid", "minVersion": "0.150.0"}])
    info = m.update_info("codex", "0.153.4", [{"id": "gpt-old"}])
    assert info == {"latest": "0.172.0", "updatable": True,
                    "newModels": [{"id": "gpt-new", "label": "GPT New"}]}
    assert m.update_info("codex", "0.172.0", []) == {}


def test_versions_order():
    assert m.version_key("0.171.0-alpha.13") < m.version_key("0.171.0") < m.version_key("0.172.0")
    assert m.version_key("0.153.4") < m.version_key("0.171.0-alpha.2")


def test_run_update_runs_the_installer(monkeypatch):
    calls = []
    monkeypatch.setattr(m, "install_method", lambda _b: {"kind": "npm", "argv": ["npm", "i"], "env": {"X": "1"}})
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: calls.append((argv, kw["env"]["X"]))
                        or subprocess.CompletedProcess(argv, 0, "", ""))
    assert m.run_update("codex") == {"ok": True, "kind": "npm"}
    assert calls == [(["npm", "i"], "1")]
    monkeypatch.setattr(m, "install_method", lambda _b: None)
    assert m.run_update("codex") == {"ok": False, "error": "manual"}


def test_bundled_manifest_is_read_offline(monkeypatch):
    monkeypatch.setattr(m, "_fetch_json", lambda _url: None)
    m._cache.clear()
    models = m.manifest_models()
    assert models and all(model["slug"].startswith("gpt-") for model in models)
    m._cache.clear()

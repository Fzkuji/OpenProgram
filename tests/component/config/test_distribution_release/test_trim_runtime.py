"""A shipped runtime leaves out what it never runs.

trim-runtime.py removes package test suites, debug symbols and dugite Git's
Credential Manager; the App's docs leave out the design pages and send a link
to one to the public site.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
_spec = importlib.util.spec_from_file_location("trim_runtime", ROOT / "scripts" / "release" / "trim-runtime.py")
trim_runtime = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(trim_runtime)


def _write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_package_test_suites_and_debug_symbols_go_and_the_code_stays(tmp_path):
    site = tmp_path / "site-packages"
    keep = [
        _write(site / "numpy" / "__init__.py"),
        _write(site / "numpy" / "testing" / "__init__.py"),
        _write(site / "numpy" / "_core" / "multiarray.py"),
        _write(site / "objc" / "_objc.so"),
        _write(site / "pytest_like-1.0.dist-info" / "RECORD"),
    ]
    gone = [
        _write(site / "numpy" / "_core" / "tests" / "test_a.py"),
        _write(site / "joblib" / "test" / "test_b.py"),
        _write(site / "objc" / "_objc.so.dSYM" / "Contents" / "DWARF" / "_objc.so"),
        _write(site / "PyObjCTest" / "__init__.py"),
    ]

    removed = trim_runtime.trim_packages(site)

    assert removed > 0
    assert all(path.exists() for path in keep)
    assert not any(path.exists() for path in gone)
    assert not (site / "PyObjCTest").exists()


def test_only_site_packages_are_trimmed_not_the_interpreter_prefix():
    roots = trim_runtime.site_packages(sys.executable)
    assert roots
    assert all(root.name == "site-packages" for root in roots)


def test_dugite_git_loses_its_credential_manager_and_keeps_git(tmp_path):
    git = tmp_path / "git"
    core = git / "libexec" / "git-core"
    keep = [
        _write(git / "bin" / "git"),
        _write(core / "git"),
        _write(core / "git-remote-https"),
        _write(core / "git-credential-store"),
        _write(core / "scalar"),
        _write(core / "mergetools" / "vimdiff"),
    ]
    gone = [
        _write(core / "git-credential-manager"),
        _write(core / "git-credential-manager.dll"),
        _write(core / "git-credential-manager.deps.json"),
        _write(core / "Avalonia.Base.dll"),
        _write(core / "libcoreclr.dylib"),
        _write(core / "createdump"),
        _write(core / "zh-Hans" / "git-credential-manager.resources.dll"),
    ]

    assert trim_runtime.trim_git(git) > 0
    assert all(path.exists() for path in keep)
    assert not any(path.exists() for path in gone)
    assert not (core / "zh-Hans").exists()


def test_a_git_without_the_manager_is_left_alone(tmp_path):
    git = tmp_path / "git"
    library = _write(git / "libexec" / "git-core" / "libsomething.so")
    assert trim_runtime.trim_git(git) == 0
    assert library.exists()


def test_a_packaged_docs_site_without_design_pages_sends_them_to_the_public_site(tmp_path, monkeypatch):
    from openprogram.interfaces.server.routes import docs as docs_route

    site = tmp_path / "docs"
    _write(site / "index.html", "<html>home</html>")
    monkeypatch.setattr(docs_route, "_site_dir", lambda: site)
    monkeypatch.setattr(docs_route, "_maybe_rebuild", lambda: None)
    monkeypatch.setattr(docs_route, "_is_source_checkout", lambda: False)
    app = FastAPI()
    docs_route.register(app)

    with TestClient(app) as client:
        assert client.get("/docs/").status_code == 200
        response = client.get("/docs/reference/design/runtime/README.html", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == (
        docs_route.PUBLIC_DOCS_URL + "reference/design/runtime/README.html")


def test_a_docs_site_with_design_pages_serves_them_itself(tmp_path, monkeypatch):
    from openprogram.interfaces.server.routes import docs as docs_route

    site = tmp_path / "docs"
    _write(site / "index.html", "<html>home</html>")
    _write(site / "reference" / "design" / "README.html", "<html>design</html>")
    monkeypatch.setattr(docs_route, "_site_dir", lambda: site)
    monkeypatch.setattr(docs_route, "_maybe_rebuild", lambda: None)
    monkeypatch.setattr(docs_route, "_is_source_checkout", lambda: False)
    app = FastAPI()
    docs_route.register(app)

    with TestClient(app) as client:
        response = client.get("/docs/reference/design/README.html")
    assert response.status_code == 200
    assert "design" in response.text

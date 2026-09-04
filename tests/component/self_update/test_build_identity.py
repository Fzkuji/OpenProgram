"""Baked wheel identity and authenticated process identity."""
from pathlib import Path
import subprocess
import sys
import zipfile
import shutil

from fastapi import FastAPI
from starlette.testclient import TestClient


def test_real_wheel_contains_build_revision(tmp_path):
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--no-index", "--no-deps", "--no-build-isolation", "--wheel-dir", str(tmp_path), str(root)],
        capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stderr[-3000:]
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip()
    with zipfile.ZipFile(next(tmp_path.glob("openprogram-*.whl"))) as wheel:
        assert wheel.read("openprogram/_build_revision.txt").decode().strip() == head + ("-dirty" if dirty else "")


def test_real_editable_wheel_still_builds(tmp_path):
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [sys.executable, "-c", "from setuptools.build_meta import build_editable; import sys; build_editable(sys.argv[1])", str(tmp_path)],
        cwd=root, capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stderr[-4000:]
    assert "Customization incompatible with editable install" not in result.stderr, result.stderr[:4000]
    assert list(tmp_path.glob("openprogram-*.whl"))


def test_packaged_revision_is_frozen_before_first_request(tmp_path, monkeypatch):
    import openprogram
    from openprogram.webui.routes import misc
    package = tmp_path / "site-packages/openprogram"
    package.mkdir(parents=True)
    marker = package / "_build_revision.txt"
    marker.write_text("a" * 40 + "\n")
    monkeypatch.setattr(openprogram, "__file__", str(package / "__init__.py"))
    monkeypatch.setattr(misc, "_HEAD_SHA", None)
    misc.register(FastAPI())
    marker.write_text("b" * 40 + "\n")
    assert misc._head_sha() == "a" * 40


def test_authenticated_diagnostics_identify_pid_and_owner(monkeypatch):
    import os
    from types import SimpleNamespace
    from openprogram.webui.routes import misc
    from openprogram.webui.owner_auth import OwnerAuthMiddleware, OwnerAuthState
    owner = "owner/install/0123456789abcdef"
    state = OwnerAuthState.from_raw_token(bytes(range(32)), owner_principal_id=owner, bind_host="127.0.0.1", port=18100, allowed_origins=())
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: SimpleNamespace(list_sessions=lambda **_: [], count_recent_nodes=lambda _: 0))
    app = FastAPI()
    misc.register(app)
    with TestClient(OwnerAuthMiddleware(app, auth_state=state), base_url="http://127.0.0.1:18100") as client:
        assert client.get("/api/diagnostics").status_code == 401
        assert client.get("/healthz").json() == {"status": "ok"}
        data = client.get("/api/diagnostics", headers={"Authorization": f"Bearer {state.token}"}).json()
        assert data["worker_pid"] == os.getpid()
        assert data["principal_id"] == owner


def test_source_archive_does_not_inherit_a_parent_repository_revision(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                    "commit", "--allow-empty", "-qm", "parent"], cwd=tmp_path, check=True)
    source = tmp_path / "source"
    (source / "openprogram").mkdir(parents=True)
    (source / "openprogram/__init__.py").write_text("")
    (source / "pyproject.toml").write_text('[build-system]\nrequires=["setuptools", "wheel"]\nbuild-backend="setuptools.build_meta"\n[project]\nname="openprogram"\nversion="0.0.0"\n')
    shutil.copy2(Path(__file__).resolve().parents[3] / "setup.py", source / "setup.py")
    result = subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-index", "--no-deps", "--no-build-isolation",
                             "--wheel-dir", str(tmp_path / "wheels"), str(source)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(next((tmp_path / "wheels").glob("*.whl"))) as wheel:
        assert wheel.read("openprogram/_build_revision.txt").strip() == b""


def test_doctor_exposes_stable_check_ids(monkeypatch):
    from openprogram.cli.commands import doctor
    def healthy():
        return True, "Healthy", "checked"
    monkeypatch.setattr(doctor, "CHECKS", (healthy,))
    assert doctor.run_checks() == [{"id": "healthy", "ok": True, "label": "Healthy", "detail": "checked"}]

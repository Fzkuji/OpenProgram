"""Application metadata in the existing owner-approved Program source catalog."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import site
import subprocess
import tempfile
import venv

from openprogram.paths import get_state_dir
from openprogram.programs import _programs

ID = re.compile(r"[a-z][a-z0-9._-]{0,95}\Z")
CAPABILITIES = {"model.invoke", "storage.app", "files.project.read"}


def home() -> Path:
    return get_state_dir() / "applications"


def contained(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative or Path(relative).is_absolute():
        raise ValueError("expected a relative package path")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("path escapes application directory")
    return path


def manifest(root: Path) -> dict:
    raw = json.loads((root / "application.json").read_text())
    if not isinstance(raw, dict) or not ID.fullmatch(str(raw.get("id", ""))):
        raise ValueError("invalid application id")
    for key in ("title", "version"):
        if not isinstance(raw.get(key), str) or not 0 < len(raw[key]) <= 160:
            raise ValueError(f"invalid {key}")
    raw.setdefault("scope", "global")
    raw.setdefault("capabilities", [])
    raw.setdefault("dataSchema", 1)
    raw.setdefault("operations", {})
    if raw["scope"] not in {"global", "project"}:
        raise ValueError("scope must be global or project")
    if not isinstance(raw["capabilities"], list) or any(not isinstance(x, str) or x not in CAPABILITIES for x in raw["capabilities"]):
        raise ValueError("unsupported application capability")
    if type(raw["dataSchema"]) is not int or raw["dataSchema"] < 1:
        raise ValueError("invalid dataSchema")
    ui = raw.setdefault("ui", {})
    if not isinstance(ui, dict):
        raise ValueError("ui must be an object")
    ui.setdefault("root", ".")
    ui.setdefault("entry", "index.html")
    entry = contained(contained(root, ui.get("root", ".")), ui.get("entry", "index.html"))
    if not entry.is_file() or entry.suffix.lower() != ".html":
        raise ValueError("ui.entry must be an existing HTML file")
    if not isinstance(raw["operations"], dict):
        raise ValueError("operations must be an object")
    from jsonschema import Draft202012Validator
    for name, operation in raw["operations"].items():
        if not ID.fullmatch(name) or not isinstance(operation, dict):
            raise ValueError("invalid operation")
        for field in ("input", "output"):
            Draft202012Validator.check_schema(operation.get(field, {}))
    backend = raw.get("backend")
    if backend:
        if not isinstance(backend, dict):
            raise ValueError("backend must be an object")
        if backend.get("kind") != "python":
            raise ValueError("only Python backends are supported")
        entrypoint = backend.get("entry", "")
        if not re.fullmatch(r"[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*:[a-zA-Z_]\w*", entrypoint):
            raise ValueError("backend.entry must be module:object")
        module = entrypoint.split(":")[0].replace(".", "/")
        if not (contained(root, module + ".py").is_file() or contained(root, module + "/__init__.py").is_file()):
            raise ValueError("backend module is missing")
        deps = backend.get("dependencies", [])
        if not isinstance(deps, list) or any(not isinstance(d, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?==[A-Za-z0-9_.+-]+", d) for d in deps):
            raise ValueError("backend dependencies must be exact name==version requirements")
    elif raw["operations"]:
        raise ValueError("operations require a backend")
    return raw


def installed() -> list[dict]:
    return [dict(r["application"]) for r in _programs._read_program_sources()
            if r.get("kind") == "application" and isinstance(r.get("application"), dict)]


def get(app_id: str, *, enabled: bool = True) -> dict:
    for app in installed():
        if app["id"] == app_id and (not enabled or app.get("enabled")):
            return app
    raise FileNotFoundError("application is not installed or enabled")


def python_path(digest: str) -> Path:
    return home() / "environments" / digest / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def install(path: str, *, replace: bool = False, trust: bool = False) -> dict:
    source = Path(path).expanduser().resolve(strict=True)
    if not source.is_dir() or source.is_relative_to(home().resolve()):
        raise ValueError("select an application source directory outside managed storage")
    definition = manifest(source)
    if definition.get("backend") and not trust:
        raise ValueError("Python backends run trusted local code; pass trust=true after reviewing the source")
    staging_root = home() / "staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=staging_root) as temp:
        staging = Path(temp) / "package"
        staging.mkdir()
        size = 0
        for folder, dirs, files in os.walk(source, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in {".git", "node_modules", "__pycache__", ".venv"})
            for name in dirs + files:
                if (Path(folder) / name).is_symlink():
                    raise ValueError("application packages cannot contain symlinks")
            for name in sorted(files):
                if name.endswith((".pyc", ".pyo")):
                    continue
                src = Path(folder) / name
                if not src.is_file():
                    raise ValueError("application package contains a non-regular file")
                size += src.stat().st_size
                if size > 100 * 1024 * 1024:
                    raise ValueError("application package exceeds 100 MiB")
                dst = staging / src.relative_to(source)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
        definition = manifest(staging)
        digest = package_digest(staging)
        target = home() / "versions" / digest
        target.parent.mkdir(parents=True, exist_ok=True)
        backend = definition.get("backend")
        if backend:
            executable = python_path(digest)
            ready = executable.parent.parent / ".openprogram-ready"
            if not ready.exists():
                if executable.parent.parent.exists():
                    shutil.rmtree(executable.parent.parent)
                # The base environment is read-only; application dependencies
                # install only into this version's own environment.
                venv.EnvBuilder(system_site_packages=True, with_pip=True, symlinks=os.name != "nt").create(executable.parent.parent)
                # Inherit the host framework's dependency locations after this
                # application's own site-packages, including a host virtualenv.
                environment = executable.parent.parent
                sites = list(environment.glob("lib/python*/site-packages"))
                if os.name == "nt":
                    sites = [environment / "Lib" / "site-packages"]
                for location in sites:
                    (location / "openprogram-host.pth").write_text("\n".join(site.getsitepackages()) + "\n")
                dependencies = backend.get("dependencies", [])
                if dependencies:
                    subprocess.run([str(executable), "-m", "pip", "install", "--disable-pip-version-check", *dependencies], check=True, capture_output=True, timeout=300)
            probe = (
                "import importlib,json,sys; sys.dont_write_bytecode=True; "
                "sys.path.insert(0,sys.argv[1]); "
                "m,n=sys.argv[2].split(':'); ops=getattr(importlib.import_module(m),n); "
                "assert isinstance(ops,dict), 'backend entry must be an operation mapping'; "
                "assert all(callable(ops.get(k)) for k in json.loads(sys.argv[3])), 'declared operation missing or not callable'"
            )
            import openprogram
            probe_env = dict(os.environ)
            probe_env["PYTHONPATH"] = str(Path(openprogram.__file__).resolve().parent.parent)
            try:
                subprocess.run([str(executable), "-c", probe, str(staging), backend["entry"], json.dumps(list(definition["operations"]))],
                               check=True, capture_output=True, timeout=30, env=probe_env)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                raise ValueError("application backend failed its import/operation check") from exc
            if package_digest(staging) != digest:
                raise ValueError("application backend changed its package during validation")
            ready.write_text(digest)
        result = {**definition, "digest": digest, "enabled": True, "source": str(source)}

        def activate(rows):
            previous = next((r["application"] for r in rows if r.get("kind") == "application" and r.get("application", {}).get("id") == definition["id"]), None)
            if previous:
                if previous["source"] != str(source):
                    raise ValueError("application id belongs to a different source")
                if previous["digest"] != digest and not replace:
                    raise ValueError("application already installed; use replace")
                if previous["dataSchema"] != definition["dataSchema"] or previous["scope"] != definition["scope"]:
                    raise ValueError("dataSchema/scope changes require an explicit migration; upgrade not activated")
            if not target.exists():
                staging.rename(target)
            if package_digest(target) != digest:
                raise ValueError("installed application content has changed")
            kept = [r for r in rows if not (r.get("kind") == "application" and r.get("application", {}).get("id") == definition["id"])]
            kept.append({"kind": "application", "path": str(target), "source": str(source), "application": result})
            _programs._write_program_sources(kept)

        _programs._update_program_sources(activate)
        return result


def package_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("installed application contains a symlink")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"}:
            content = path.read_bytes()
            digest.update(json.dumps([path.relative_to(root).as_posix(), len(content)]).encode())
            digest.update(content)
    return digest.hexdigest()


def remove(app_id: str) -> None:
    get(app_id, enabled=False)
    def mutate(rows):
        _programs._write_program_sources([r for r in rows if not (r.get("kind") == "application" and r.get("application", {}).get("id") == app_id)])
    _programs._update_program_sources(mutate)


def configure(app_id: str, **changes) -> dict:
    allowed = {"enabled", "hidden", "display_title"}
    if set(changes) - allowed:
        raise ValueError("unsupported application setting")
    for key, value in changes.items():
        if key in {"enabled", "hidden"} and type(value) is not bool:
            raise ValueError(f"{key} must be boolean")
        if key == "display_title" and (not isinstance(value, str) or not 1 <= len(value) <= 160):
            raise ValueError("display_title must contain 1–160 characters")
    result = {}
    def mutate(rows):
        for row in rows:
            if row.get("kind") == "application" and row.get("application", {}).get("id") == app_id:
                row["application"].update(changes)
                result.update(row["application"])
        if not result:
            raise FileNotFoundError(app_id)
        _programs._write_program_sources(rows)
    _programs._update_program_sources(mutate)
    return result

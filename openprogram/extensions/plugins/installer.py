"""Plugin installer.

``source`` is ``npm``, ``git`` or ``path``; every call returns ``{success,
log}``. A plugin is declarative, so a package whose manifest names code is
refused here — a cloned or npm-installed copy is removed again — with the
message to package it as an application instead.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import paths


def _refused(directory: Path) -> str:
    """The refusal for the plugin in ``directory``, ``""`` when declarative."""
    from .manifest import code_refusal, parse_manifest_dir
    manifest = parse_manifest_dir(directory)
    return code_refusal(manifest) if manifest is not None else ""


def _run(cmd: list[str], cwd: str | None = None) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=600,
        )
        log = (r.stdout or "") + (r.stderr or "")
        return r.returncode == 0, log
    except Exception as e:
        return False, f"exec error: {e}"


def install(source: str, spec: str, ref: str | None = None, upgrade: bool = False) -> dict[str, Any]:
    source = (source or "").lower().strip()
    spec = (spec or "").strip()
    if not spec:
        return {"success": False, "log": "empty spec"}

    if source == "pip":
        return {"success": False, "log": (
            "pip packages are Python code, and plugins carry no code. Package it as an "
            "application (application.json) and run `openprogram programs install <dir>`.")}
    if source == "npm":
        return _install_npm(spec)
    if source == "git":
        return _install_git(spec, ref)
    if source == "path":
        return _install_path(spec)
    return {"success": False, "log": f"unknown source: {source}"}


def _install_npm(spec: str) -> dict[str, Any]:
    if not shutil.which("npm"):
        return {"success": False, "log": "npm not found in PATH"}
    prefix = str(paths.npm_root())
    # 第一次安装前确保 package.json 存在
    pj = Path(prefix) / "package.json"
    if not pj.exists():
        pj.write_text(json.dumps({"name": "openprogram-plugins", "private": True}), encoding="utf-8")
    # node_tool_cmd routes npm.cmd through cmd.exe on Windows (a .cmd
    # shim can't be exec'd by CreateProcess with shell=False).
    from openprogram.host._platform.terminal import node_tool_cmd
    ok, log = _run(node_tool_cmd(["npm", "install", "--prefix", prefix, spec]))
    if ok:
        name = spec.rsplit("@", 1)[0] if spec.count("@") > (1 if spec.startswith("@") else 0) else spec
        refusal = _refused(paths.npm_modules_dir() / name)
        if refusal:
            _run(node_tool_cmd(["npm", "uninstall", "--prefix", prefix, name]))
            return {"success": False, "log": log + "\n" + refusal}
    return {"success": ok, "log": log}


def _install_git(url: str, ref: str | None) -> dict[str, Any]:
    if not shutil.which("git"):
        return {"success": False, "log": "git not found in PATH"}
    # 名字取 url 末段
    name = url.rstrip("/").split("/")[-1]
    if name.endswith(".git"):
        name = name[:-4]
    try:
        paths.sanitize_name(name)
    except ValueError:
        return {"success": False, "log": f"derived name invalid: {name}"}
    dest = paths.plugins_dir() / name
    if dest.exists():
        return {"success": False, "log": f"{dest} already exists"}
    ok, log = _run(["git", "clone", url, str(dest)])
    if ok and ref:
        ok2, log2 = _run(["git", "checkout", ref], cwd=str(dest))
        log += "\n" + log2
        if not ok2:
            return {"success": False, "log": log}
    refusal = _refused(dest) if ok else ""
    if refusal:
        shutil.rmtree(dest, ignore_errors=True)
        return {"success": False, "log": log + "\n" + refusal}
    return {"success": ok, "log": log}


def _install_path(abs_path: str) -> dict[str, Any]:
    """Link a plugin directory into the plugins directory.

    The plugin keeps living where its author edits it: the link makes it
    an installed ``link`` plugin, so a reload picks up every change.
    """
    from .manifest import parse_manifest_dir
    p = Path(abs_path).expanduser()
    if not p.is_absolute() or not p.is_dir():
        return {"success": False, "log": f"not an absolute existing directory: {abs_path}"}
    mf = parse_manifest_dir(p)
    if mf is None:
        return {"success": False, "log": f"no plugin manifest in {p}"}
    from .manifest import code_refusal
    if code_refusal(mf):
        return {"success": False, "log": code_refusal(mf)}
    try:
        paths.sanitize_name(mf.name)
    except ValueError as e:
        return {"success": False, "log": str(e)}
    link = paths.plugins_dir() / mf.name
    target = p.resolve()
    if link.is_symlink() and link.resolve() == target:
        return {"success": True, "name": mf.name, "log": f"{link} already links {target}"}
    if link.exists() or link.is_symlink():
        return {"success": False, "log": f"{link} already exists; uninstall {mf.name} first"}
    link.symlink_to(target, target_is_directory=True)
    # The owner pointed at this directory: trust it unless they ruled otherwise.
    from . import trust
    if mf.name not in trust._load():
        trust.set_trust(mf.name, "community")
    return {"success": True, "name": mf.name, "log": f"linked {link} -> {target}"}


def uninstall(name: str) -> dict[str, Any]:
    """按 plugin 当前来源决定卸载方式。"""
    from .loader import get_plugin  # 延迟导入
    paths.sanitize_name(name)
    p = get_plugin(name)
    if not p:
        return {"success": False, "log": f"unknown plugin: {name}"}
    src = p.manifest.source_kind
    if src == "npm":
        if not shutil.which("npm"):
            return {"success": False, "log": "npm not found"}
        from openprogram.host._platform.terminal import node_tool_cmd
        ok, log = _run(node_tool_cmd(["npm", "uninstall", "--prefix", str(paths.npm_root()), name]))
        return {"success": ok, "log": log}
    if src == "link":
        for child in paths.plugins_dir().iterdir():
            if child.is_symlink() and child.resolve() == Path(p.manifest.root).resolve():
                child.unlink()
                return {"success": True, "log": f"removed link {child}; {p.manifest.root} is untouched"}
        return {"success": False, "log": f"no link to {p.manifest.root} in {paths.plugins_dir()}"}
    if src == "path":
        d = Path(p.manifest.root)
        # 真正的包含判断：<plugins>-backup/ 也以 <plugins> 开头，不能被 rmtree
        if d.is_dir() and d.resolve().is_relative_to(paths.plugins_dir().resolve()):
            shutil.rmtree(d, ignore_errors=True)
            return {"success": True, "log": f"removed {d}"}
        return {"success": False, "log": f"refusing to remove path outside plugins dir: {d}"}
    if src == "project":
        pin = paths.project_pins()
        if not pin.exists():
            return {"success": False, "log": "no project pin file"}
        try:
            data = json.loads(pin.read_text(encoding="utf-8"))
        except Exception as e:
            return {"success": False, "log": f"parse pin: {e}"}
        entries = data.get("plugins", []) if isinstance(data, dict) else []
        kept = [e for e in entries if isinstance(e, dict) and Path(e.get("path", "")).resolve() != Path(p.manifest.root).resolve()]
        if isinstance(data, dict):
            data["plugins"] = kept
        else:
            data = {"plugins": kept}
        pin.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"success": True, "log": "removed from project pins"}
    return {"success": False, "log": f"unknown source_kind: {src}"}

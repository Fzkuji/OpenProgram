"""``openprogram browser`` handlers — install / status / refresh / reset / list / rm."""
from __future__ import annotations

import subprocess
import shutil
import sys
from pathlib import Path


def _python_pkg_present(name: str) -> bool:
    """Cheap import probe for status checks."""
    import importlib.util
    return importlib.util.find_spec(name) is not None


def _pip_install(spec: str) -> subprocess.CompletedProcess:
    """Cross-platform ``pip install``.

    Calls ``python -m pip`` rather than bare ``pip`` because the latter
    isn't resolvable as a Windows executable in ``subprocess.run`` (no
    ``.exe`` auto-append outside the shell) — would raise
    ``PermissionError [WinError 5]`` on Windows.
    """
    return subprocess.run([sys.executable, "-m", "pip", "install", spec])


def _kill_chrome_profile() -> None:
    """Best-effort kill of any sidecar Chrome locked into our profile.

    POSIX: ``pkill -9 -f openprogram/chrome-profile``.
    Windows: ``taskkill /F /IM chrome.exe`` filtered by command line via
    WMIC is fragile; we use the simpler approach of ``taskkill`` against
    chrome.exe processes whose command line mentions the profile path.
    Failure is non-fatal — the caller's ``rmtree`` will still run.
    """
    needle = "openprogram/chrome-profile" if sys.platform != "win32" else r"openprogram\chrome-profile"
    try:
        if sys.platform == "win32":
            # WMIC is deprecated but still ships on Win10/11. Find PIDs
            # whose CommandLine contains the profile path, then kill.
            res = subprocess.run(
                ["wmic", "process", "where",
                 f"CommandLine like '%{needle}%' and Name='chrome.exe'",
                 "get", "ProcessId"],
                capture_output=True, text=True, timeout=5,
            )
            for line in (res.stdout or "").splitlines():
                line = line.strip()
                if line.isdigit():
                    subprocess.run(
                        ["taskkill", "/F", "/PID", line],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
        else:
            subprocess.run(
                ["pkill", "-9", "-f", needle],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        # No pkill / wmic / taskkill on PATH, or all chrome procs already
        # gone. Caller's rmtree still does the visible work.
        pass


def _cmd_browser_install(target: str) -> int:
    """Install browser-tool dependencies. Pure shell-out — no agent involved."""
    targets = ["playwright", "patchright", "camoufox", "agent"] if target == "all" else [target]
    rc = 0
    for t in targets:
        print(f"\n=== installing {t} ===")
        if t == "playwright":
            r1 = _pip_install("playwright>=1.45.0")
            r2 = subprocess.run(["playwright", "install", "chromium"]) if shutil.which("playwright") else r1
            if r1.returncode or (hasattr(r2, "returncode") and r2.returncode):
                rc = 1
        elif t == "patchright":
            r1 = _pip_install("patchright>=1.40")
            r2 = subprocess.run(["patchright", "install", "chromium"]) if shutil.which("patchright") else r1
            if r1.returncode or (hasattr(r2, "returncode") and r2.returncode):
                rc = 1
        elif t == "camoufox":
            r1 = _pip_install("camoufox>=0.4.0")
            r2 = subprocess.run(["camoufox", "fetch"]) if shutil.which("camoufox") else r1
            if r1.returncode or (hasattr(r2, "returncode") and r2.returncode):
                rc = 1
        elif t == "agent":
            npm_path = shutil.which("npm")
            if not npm_path:
                print("npm not found — install Node.js first.")
                rc = 1
                continue
            # Pass the resolved path (e.g. ``C:\Program Files\nodejs\npm.CMD``
            # on Windows) rather than the bare name. Windows' subprocess
            # without ``shell=True`` can't execute ``.cmd``/``.bat`` shims
            # by bare name — would raise ``FileNotFoundError [WinError 2]``.
            r1 = subprocess.run([npm_path, "install", "-g", "agent-browser"])
            ab_path = shutil.which("agent-browser")
            r2 = subprocess.run([ab_path, "install"]) if ab_path else r1
            if r1.returncode or (hasattr(r2, "returncode") and r2.returncode):
                rc = 1
        else:
            print(f"Unknown target: {t}")
            rc = 1
    return rc


def _cmd_browser_status() -> int:
    """Show what's installed, sidecar state, saved login count."""
    from openprogram.functions.tools.browser._chrome_bootstrap import (
        port_file, sidecar_dir, is_port_listening,
    )

    print("Installations:")
    print(f"  playwright       {'✓' if _python_pkg_present('playwright') else '✗'}")
    print(f"  patchright       {'✓' if _python_pkg_present('patchright') else '✗'}")
    print(f"  camoufox         {'✓' if _python_pkg_present('camoufox') else '✗'}")
    print(f"  agent-browser    {'✓' if shutil.which('agent-browser') or shutil.which('npx') else '✗'}")

    print("\nSidecar Chrome:")
    sd = sidecar_dir()
    if sd.exists():
        size_mb = sum(f.stat().st_size for f in sd.rglob("*") if f.is_file()) / 1024 / 1024
        print(f"  profile dir: {sd} ({size_mb:.0f} MB)")
    else:
        print(f"  profile dir: (not yet created — runs lazily on first use)")
    pf = port_file()
    if pf.exists():
        try:
            port = int(pf.read_text().strip())
            alive = is_port_listening(port)
            print(f"  port file: {pf} → :{port} {'(LISTENING)' if alive else '(stale, not listening)'}")
        except (OSError, ValueError):
            print(f"  port file: {pf} (unreadable)")
    else:
        print("  port file: (none — sidecar not started)")

    print("\nSaved logins:")
    state_dir = Path.home() / ".openprogram" / "browser-states"
    if state_dir.exists():
        states = sorted(state_dir.glob("*.json"))
        if states:
            for p in states:
                kb = p.stat().st_size / 1024
                print(f"  {p.stem:<40} {kb:>6.1f} KB")
        else:
            print(f"  (none under {state_dir})")
    else:
        print(f"  (directory {state_dir} doesn't exist yet)")
    return 0


def _cmd_browser_refresh() -> int:
    """Re-copy the real Chrome profile to the sidecar."""
    from openprogram.functions.tools.browser._chrome_bootstrap import (
        sidecar_dir, port_file,
    )

    sd = sidecar_dir()
    if sd.exists():
        _kill_chrome_profile()
        shutil.rmtree(sd, ignore_errors=True)
        print(f"Removed old sidecar profile at {sd}")
    pf = port_file()
    if pf.exists():
        pf.unlink()
        print(f"Cleared port file {pf}")
    print("Next browser tool open() will re-copy your real Chrome profile.")
    return 0


def _cmd_browser_reset() -> int:
    """Full reset — sidecar + states + port file."""
    _kill_chrome_profile()
    sd = Path.home() / ".openprogram" / "chrome-profile"
    if sd.exists():
        shutil.rmtree(sd, ignore_errors=True)
        print(f"Removed sidecar profile {sd}")
    states = Path.home() / ".openprogram" / "browser-states"
    if states.exists():
        shutil.rmtree(states, ignore_errors=True)
        print(f"Removed saved logins {states}")
    pf = Path.home() / ".openprogram" / "browser-cdp-port"
    if pf.exists():
        pf.unlink()
        print(f"Removed port file {pf}")
    print("Browser tool fully reset. Next open() bootstraps clean.")
    return 0


def _cmd_browser_list() -> int:
    """List saved browser logins."""
    state_dir = Path.home() / ".openprogram" / "browser-states"
    if not state_dir.exists():
        print(f"(no saved logins — directory doesn't exist: {state_dir})")
        return 0
    entries = sorted(state_dir.glob("*.json"))
    if not entries:
        print(f"(no saved logins under {state_dir})")
        return 0
    print(f"Saved logins ({len(entries)}):")
    for p in entries:
        size_kb = p.stat().st_size / 1024
        print(f"  {p.stem:<40} {size_kb:>6.1f} KB")
    return 0


def _cmd_browser_rm(name: str) -> int:
    """Delete a saved login."""
    state_dir = Path.home() / ".openprogram" / "browser-states"
    candidates = [state_dir / f"{name}.json", state_dir / name]
    for p in candidates:
        if p.exists():
            p.unlink()
            print(f"Removed {p}")
            return 0
    print(f"No saved login found for {name!r} (looked in {state_dir})")
    return 1

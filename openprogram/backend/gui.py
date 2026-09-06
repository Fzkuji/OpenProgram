"""Mandatory local isolation for the persistent GUI interpreter.

This entry owns its scratch directory and interpreter choice. Ordinary backend
launch settings cannot add filesystem, network or desktop authority to it.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import tempfile

from openprogram import sandbox


def _profile(scratch: Path) -> str:
    outer = sandbox.resolve_policy() or sandbox.SandboxPolicy()
    policy = replace(outer, writable_roots=(), network=False, pass_env=(), host_process_info=False)
    profile = sandbox._seatbelt_profile(str(scratch), policy, private_tmp=True, allow_subprocesses=False)
    # Append a restrictive intersection, never an allow that could reopen an
    # outer deny. No recoverable-trash root or general backend fallback.
    reads = {
        str(scratch), str(Path(sys.executable).resolve()),
        str(Path(sysconfig.get_path("stdlib")).resolve()),
        str(Path(sys.base_prefix, "lib").resolve()),
        "/System/Library", "/usr/lib",
        "/System/Volumes/Preboot/Cryptexes/OS/System/Library",
        "/System/Volumes/Preboot/Cryptexes/OS/usr/lib",
    }
    exceptions = [f"(require-not (subpath {sandbox._sbpl_str(path)}))" for path in sorted(reads)]
    exceptions += [f'(require-not (literal "{path}"))' for path in
                   ("/dev/null", "/dev/zero", "/dev/random", "/dev/urandom")]
    # dyld opens the root directory and getcwd traverses ancestors. Grant only
    # those directory entries, not their descendants or file contents.
    parents = {str(parent) for root in reads for parent in Path(root).parents}
    exceptions += [f"(require-not (literal {sandbox._sbpl_str(parent)}))" for parent in sorted(parents)]
    profile += "(deny file-read* (require-all " + " ".join(exceptions) + "))\n"
    profile += (f"(deny file-write* (require-all (require-not (subpath {sandbox._sbpl_str(str(scratch))})) "
                '(require-not (literal "/dev/null"))))\n')
    profile += "(deny ipc-posix-shm)\n(deny ipc-posix-sem)\n"
    return profile


@contextmanager
def spawn_gui_python(bootstrap: str):
    """Yield a sandboxed Python child with separate byte-oriented stdio pipes.

    The caller implements bounded protocol/output handling. On any context exit,
    this function terminates and reaps the child and removes its private files.
    Unsupported platforms fail closed; they never use ordinary backend.spawn.
    """
    if sys.platform != "darwin":
        raise sandbox.SandboxUnavailable("Strict GUI Python isolation currently requires macOS")
    reason = sandbox.unavailable_reason()
    if reason:
        raise sandbox.SandboxUnavailable(f"Strict GUI Python sandbox unavailable: {reason}")
    with tempfile.TemporaryDirectory(prefix="openprogram-gui-") as directory:
        scratch = Path(directory).resolve()
        script = scratch / "bootstrap.py"
        script.write_text(bootstrap, encoding="utf-8")
        command = [str(Path(sys.executable).resolve()), "-I", "-B", "-S", "-X", "utf8", str(script)]
        process = subprocess.Popen(
            ["/usr/bin/sandbox-exec", "-p", _profile(scratch), *command],
            cwd=scratch, env={"PATH": os.defpath, "LANG": "en_US.UTF-8", "TMPDIR": str(scratch)},
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            yield process
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()

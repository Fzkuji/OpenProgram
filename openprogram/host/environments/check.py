"""Reachability check for an environment."""
from __future__ import annotations

import subprocess


def check_environment(env) -> tuple[bool, str]:
    """One quick round trip proving the environment can run a command."""
    if env.kind == "local":
        return True, "This computer"
    if env.kind == "ssh":
        from openprogram.host.backend.ssh import SshBackend
        result = SshBackend(env.ssh_target, env.workdir).run("pwd", timeout=20)
        if result.exit_code == 0:
            return True, f"Connected · {result.stdout.strip().splitlines()[-1]}"
        return False, (result.stderr.strip() or f"ssh exited {result.exit_code}")[:500]
    try:
        proc = subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"],
                              capture_output=True, text=True, timeout=20)
    except FileNotFoundError:
        return False, "Docker is not installed (docker CLI not on PATH)"
    except subprocess.TimeoutExpired:
        return False, "Docker did not answer within 20s"
    if proc.returncode != 0:
        return False, (proc.stderr.strip() or "Docker is not running")[:500]
    return True, f"Docker {proc.stdout.strip()} · image {env.image}"

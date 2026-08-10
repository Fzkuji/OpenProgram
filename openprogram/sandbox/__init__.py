"""System-level sandbox — restrict a shell command's file, process and
network access.

macOS: sandbox-exec (Seatbelt).  Linux: bubblewrap (bwrap).

The policy is resolved from ``~/.openprogram/config.json`` (the
``sandbox.*`` keys in ``config_schema.SETTINGS``) at the moment a command
is wrapped. That matters more than it sounds: the switch used to live on
a ``ContextVar`` and was lost at every boundary that starts a fresh
context — the web UI's asyncio task handing work to a bare thread, the
``spawn`` subprocess behind ``@agentic_function``, and any nested CLI.
A file every process reads cannot be lost at a boundary, and cannot be
skipped by an approval-layer bypass either, because it is read below the
approval layer. Callers that already hold a policy pass it explicitly to
``wrap_command``.

What the boundary is, on both platforms:

* writes are confined to the working directory plus configured roots
* reads are open EXCEPT the credential globs in ``deny_read``
* the network is off unless ``sandbox.network`` is on
* the child environment is an allowlist, so API keys do not reach it
* execution is unrestricted — children inherit the sandbox, so filtering
  binaries by path buys nothing (``/bin/bash -c`` runs arbitrary code
  either way) and breaks git/python3/make, whose ``/usr/bin`` entries are
  shims into the developer-tools directory
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sys
from dataclasses import dataclass

log = logging.getLogger(__name__)

MODE_OFF = "off"
MODE_WORKSPACE_WRITE = "workspace-write"
MODES = (MODE_OFF, MODE_WORKSPACE_WRITE)

ON_UNAVAILABLE_REFUSE = "refuse"
ON_UNAVAILABLE_WARN = "warn"
ON_UNAVAILABLE = (ON_UNAVAILABLE_REFUSE, ON_UNAVAILABLE_WARN)

# Loaded, not just present. Both reference harnesses ship a deny-read
# engine with an empty list and close the loop on egress instead; that
# reasoning does not carry here, because the memory writer is an egress
# channel that never touches the network — anything it writes returns to
# a later session's context.
DEFAULT_DENY_READ: tuple[str, ...] = (
    "~/.ssh/**",
    "~/.aws/**",
    "~/.gnupg/**",
    "~/.openprogram/auth/**",
    "~/.claude.json",
    "~/.claude/.credentials.json",
    "~/.config/gh/**",
    "~/.netrc",
    "~/Library/Keychains/**",
    "**/.env",
)

# Writes that would let a sandboxed command escape by arranging for code
# to run outside the sandbox later. Empty by default and not because the
# engine is unloaded: the always-on entry is the agentics directory (see
# ``_agentics_dir``), which no config can remove. Git hooks and git config
# are the other escape of this shape and are deliberately NOT default —
# measured, `git init` and `git clone` both write `.git/hooks/`, so
# denying it fails them outright. Blocking those needs the escalation path
# (repair-order step 5) to be usable, and it is opt-in until then.
DEFAULT_DENY_WRITE: tuple[str, ...] = ()

# The child keeps these and nothing else. A denylist of *KEY*/*TOKEN*
# patterns would have to guess every naming convention, and a list derived
# from the provider registry still misses whatever is not registered; an
# allowlist only has to know what a toolchain needs, and a provider added
# tomorrow is dropped without anyone updating anything.
_ENV_ALLOW = frozenset({
    "PATH", "HOME", "SHELL", "USER", "LOGNAME", "TERM", "TMPDIR", "TMP",
    "TEMP", "TZ", "PWD", "OLDPWD", "LANG", "LANGUAGE", "COLUMNS", "LINES",
})
_ENV_ALLOW_PREFIXES = ("LC_",)

# The floor under ``sandbox.pass_env``: the escape hatch that lets a user
# add a variable back must not be able to hand a credential to every
# command by accident.
_SECRET_NAME = re.compile(
    r"_?(API_?KEY|TOKEN|PASSWORD|PASSWD|PRIVATE_?KEY|SECRET|CREDENTIALS?)$",
    re.IGNORECASE,
)

_CHAR_DEVICES = ("/dev/null", "/dev/zero", "/dev/random", "/dev/urandom",
                 "/dev/tty")


class SandboxUnavailable(RuntimeError):
    """Config asks for a sandbox the platform cannot provide."""


@dataclass(frozen=True)
class SandboxPolicy:
    """One resolved policy. ``writable_roots`` is *extra* — the working
    directory is always writable."""
    writable_roots: tuple[str, ...] = ()
    deny_read: tuple[str, ...] = DEFAULT_DENY_READ
    deny_write: tuple[str, ...] = DEFAULT_DENY_WRITE
    network: bool = False
    pass_env: tuple[str, ...] = ()


# --- availability ----------------------------------------------------------

def unavailable_reason() -> str | None:
    """Why the sandbox cannot run here, or None when it can."""
    if sys.platform == "darwin":
        if os.path.exists("/usr/bin/sandbox-exec"):
            return None
        return "macOS needs /usr/bin/sandbox-exec"
    if sys.platform.startswith("linux"):
        if shutil.which("bwrap"):
            return None
        return "Linux needs bubblewrap (install the `bubblewrap` package)"
    return f"no sandbox backend for platform {sys.platform!r}"


def is_available() -> bool:
    return unavailable_reason() is None


# --- policy resolution -----------------------------------------------------

def _config_section() -> dict:
    try:
        from openprogram.setup import _read_config
        return (_read_config().get("sandbox") or {})
    except Exception:  # noqa: BLE001 — a broken config must not break bash
        return {}


def _with_hard_floor(policy: SandboxPolicy) -> SandboxPolicy:
    agentics = _agentics_dir()
    if agentics in policy.deny_write:
        return policy
    return SandboxPolicy(
        writable_roots=policy.writable_roots,
        deny_read=policy.deny_read,
        deny_write=policy.deny_write + (agentics,),
        network=policy.network,
        pass_env=policy.pass_env,
    )


def resolve_policy(*, required: bool = False) -> SandboxPolicy | None:
    """The policy configured right now, or None when the sandbox is off.

    Read per command, so a toggle takes effect on the next command in
    every process rather than only in the context that flipped it.
    """
    sb = _config_section()
    if (str(sb.get("mode") or MODE_OFF).strip().lower() != MODE_WORKSPACE_WRITE
            and not required):
        return None
    deny_r = sb.get("deny_read")
    deny_w = sb.get("deny_write")
    return _with_hard_floor(SandboxPolicy(
        writable_roots=tuple(sb.get("writable_roots") or ()),
        deny_read=tuple(deny_r) if isinstance(deny_r, list) else DEFAULT_DENY_READ,
        deny_write=(tuple(deny_w) if isinstance(deny_w, list)
                    else DEFAULT_DENY_WRITE),
        network=bool(sb.get("network") or False),
        pass_env=tuple(sb.get("pass_env") or ()),
    ))


def policy_to_dict(policy: SandboxPolicy) -> dict:
    policy = _with_hard_floor(policy)
    return {
        "writable_roots": list(policy.writable_roots),
        "deny_read": list(policy.deny_read),
        "deny_write": list(policy.deny_write),
        "network": policy.network,
        "pass_env": list(policy.pass_env),
    }


def policy_from_dict(data: dict) -> SandboxPolicy:
    if not isinstance(data, dict):
        raise ValueError("sandbox policy must be an object")

    def _strings(key: str) -> tuple[str, ...]:
        value = data.get(key, ())
        if not isinstance(value, (list, tuple)) or not all(
            isinstance(item, str) for item in value
        ):
            raise ValueError(f"sandbox policy {key} must be a string list")
        return tuple(value)

    network = data.get("network", False)
    if not isinstance(network, bool):
        raise ValueError("sandbox policy network must be a boolean")
    return _with_hard_floor(SandboxPolicy(
        writable_roots=_strings("writable_roots"),
        deny_read=_strings("deny_read"),
        deny_write=_strings("deny_write"),
        network=network,
        pass_env=_strings("pass_env"),
    ))


def policy_hash(policy: SandboxPolicy) -> str:
    payload = json.dumps(
        policy_to_dict(policy), sort_keys=True, separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _agentics_dir() -> str:
    """Absolute glob for the directory the function watcher auto-imports.
    Resolved rather than configured — it moves with the installation, and
    a user editing the deny list must not be able to drop it."""
    import openprogram.functions as _f
    return os.path.join(os.path.dirname(os.path.abspath(_f.__file__)),
                        "agentics", "**")


def on_unavailable() -> str:
    v = str(_config_section().get("on_unavailable") or ON_UNAVAILABLE_REFUSE)
    return v if v in ON_UNAVAILABLE else ON_UNAVAILABLE_REFUSE


def set_mode(enabled: bool) -> None:
    """Persist the on/off state. Used by the ``/sandbox`` toggles."""
    from openprogram.config_schema import set_setting
    set_setting("sandbox.mode", MODE_WORKSPACE_WRITE if enabled else MODE_OFF)


def is_enabled() -> bool:
    return resolve_policy() is not None


# --- child environment -----------------------------------------------------

def child_env(policy: SandboxPolicy, base: dict | None = None) -> dict[str, str]:
    """The environment a sandboxed child gets: an allowlist plus whatever
    ``sandbox.pass_env`` names, minus anything whose name reads as a
    credential. Dropping the rest is what removes ``OPENAI_API_KEY`` and
    its siblings from every command."""
    src = os.environ if base is None else base
    extra = frozenset(n for n in policy.pass_env if not _SECRET_NAME.search(n))
    return {
        k: v for k, v in src.items()
        if k in _ENV_ALLOW or k in extra or k.startswith(_ENV_ALLOW_PREFIXES)
    }


# --- glob translation ------------------------------------------------------

def _sbpl_str(s: str) -> str:
    """A SBPL string literal. The working directory used to be
    interpolated raw, which let a crafted path close the string and open
    a second rule — balanced payloads parse fine and widen the write
    scope."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _glob_to_regex(pattern: str) -> str | None:
    """Anchored regex for one deny-read glob, or None if it is empty."""
    p = os.path.expanduser(pattern.strip())
    if not p:
        return None
    # `dir/**` has to cover `dir` itself as well, otherwise the listing
    # (and so the key filenames) stays readable.
    tree = p.endswith("/**")
    if tree:
        p = p[:-3]
    out: list[str] = []
    i, n = 0, len(p)
    while i < n:
        if p.startswith("**/", i):
            out.append("(.*/)?")
            i += 3
        elif p.startswith("**", i):
            out.append(".*")
            i += 2
        elif p[i] == "*":
            out.append("[^/]*")
            i += 1
        elif p[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(p[i]))
            i += 1
    rx = "".join(out)
    if tree:
        rx += "(/.*)?"
    if not p.startswith(("/", "*")):
        rx = "(.*/)?" + rx
    return "^" + rx + "$"


def _static_prefix(pattern: str) -> str:
    """The part of a glob before its first wildcard."""
    idx = min((i for i in (pattern.find(c) for c in "*?[") if i >= 0),
              default=-1)
    return pattern if idx < 0 else pattern[:idx]


def _regexes_for(patterns: tuple[str, ...]) -> list[str]:
    """One regex per pattern, plus a symlink-resolved variant — on macOS
    the home directory and ``/tmp`` both resolve elsewhere, and Seatbelt
    matches the resolved path."""
    seen: list[str] = []
    for pattern in patterns:
        variants = [pattern]
        prefix = os.path.expanduser(_static_prefix(pattern))
        if prefix.startswith("/"):
            real = os.path.realpath(prefix)
            if real != prefix.rstrip("/"):
                variants.append(real + os.path.expanduser(pattern)[len(prefix):])
        for v in variants:
            rx = _glob_to_regex(v)
            if rx and rx not in seen:
                seen.append(rx)
    return seen


def _concrete_paths(patterns: tuple[str, ...]) -> list[str]:
    """Wildcard-free paths, for bubblewrap — it mounts over a path and has
    no glob matcher, so patterns like ``**/.env`` have no Linux equivalent
    and are dropped."""
    out: list[str] = []
    for pattern in patterns:
        p = pattern[:-3] if pattern.endswith("/**") else pattern
        if any(c in p for c in "*?["):
            continue
        real = os.path.realpath(os.path.expanduser(p))
        if real not in out:
            out.append(real)
    return out


# --- wrapping --------------------------------------------------------------

def wrap_command(command: str, cwd: str,
                 policy: SandboxPolicy | None = None) -> tuple[list[str], bool]:
    """Wrap *command* in a sandbox invocation. Returns ``(args, shell)``."""
    if policy is None:
        policy = SandboxPolicy()
    cwd = os.path.realpath(cwd)
    if sys.platform == "darwin":
        profile = _seatbelt_profile(cwd, policy)
        return (["/usr/bin/sandbox-exec", "-p", profile,
                 "/bin/bash", "-c", command], False)
    return (_bwrap_args(command, cwd, policy), False)


def _writable_roots(cwd: str, policy: SandboxPolicy) -> list[str]:
    roots = [cwd]
    for r in policy.writable_roots:
        real = os.path.realpath(os.path.expanduser(r))
        if real not in roots:
            roots.append(real)
    return roots


def _seatbelt_profile(cwd: str, policy: SandboxPolicy) -> str:
    lines = [
        "(version 1)",
        "(deny default)",
        "(allow process-exec)",
        "(allow process-fork)",
        # Same-sandbox only: without these the opened-up process-exec
        # would let a child signal and inspect processes on the host.
        "(allow process-info* (target same-sandbox))",
        "(allow signal (target same-sandbox))",
        "(allow ipc-posix-sem)",   # python multiprocessing
        "(allow ipc-posix-shm)",
        "(allow sysctl-read)",
        "(allow mach-lookup)",
        '(allow file-read* (subpath "/"))',
    ]
    for root in _writable_roots(cwd, policy):
        lines.append(f"(allow file-write* (subpath {_sbpl_str(root)}))")
    lines += [
        '(allow file-write* (subpath "/private/var/folders"))',
        '(allow file-write* (subpath "/private/tmp"))',
        '(allow file-write* (subpath "/tmp"))',
    ]
    # `2>/dev/null` is in most real commands and `(deny default)` blocks it.
    for dev in _CHAR_DEVICES:
        lines.append(
            "(allow file-ioctl file-read-data file-write-data "
            f'(require-all (literal "{dev}") (vnode-type CHARACTER-DEVICE)))'
        )
    if policy.network:
        lines.append("(allow network*)")
    # Deny rules last — SBPL takes the last match, so these have to come
    # after the blanket read and write grants above.
    for rx in _regexes_for(policy.deny_read):
        lit = rx.replace('"', '\\"')
        lines.append(f'(deny file-read* (regex #"{lit}"))')
        # Otherwise a denied path is still probeable by unlinking it:
        # the error distinguishes "exists" from "no such file".
        lines.append(f'(deny file-write-unlink (regex #"{lit}"))')
    for rx in _regexes_for(policy.deny_write):
        lit = rx.replace('"', '\\"')
        lines.append(f'(deny file-write* (regex #"{lit}"))')
    return "\n".join(lines) + "\n"


def _bwrap_args(command: str, cwd: str, policy: SandboxPolicy) -> list[str]:
    args = [
        "bwrap",
        "--new-session",       # bubblewrap documents this as the TIOCSTI guard
        "--die-with-parent",
        "--unshare-pid",       # else /proc/<pid>/environ of host processes is
                               # readable and any same-uid process is killable
        "--unshare-ipc",
        "--unshare-uts",
        "--cap-drop", "ALL",
        "--ro-bind", "/", "/",
        "--proc", "/proc",
        "--dev", "/dev",
    ]
    if not policy.network:
        args.append("--unshare-net")
    # tmpfs first. The other order lets `--tmpfs /tmp` cover a working
    # directory under /tmp, and the workspace silently vanishes — which is
    # where every `tempfile` staging directory lands.
    args += ["--tmpfs", "/tmp"]
    for root in _writable_roots(cwd, policy):
        args += ["--bind", root, root]
    # Only paths that exist: the root is bound read-only, so bwrap cannot
    # create a mount point for a path that is not there, and the whole
    # invocation fails with "Can't create file at <path>: Read-only file
    # system". A path that does not exist has nothing to hide anyway.
    # --perms 0000 on the tmpfs matters because --cap-drop ALL takes
    # DAC_OVERRIDE away, so the mode is enforced even when the child is
    # root inside a container.
    for path in _concrete_paths(policy.deny_read):
        if os.path.isdir(path):
            args += ["--perms", "0000", "--tmpfs", path]
        elif os.path.exists(path):
            args += ["--ro-bind", "/dev/null", path]
    for path in _concrete_paths(policy.deny_write):
        if os.path.exists(path):
            args += ["--ro-bind", path, path]
    args += ["--", "/bin/bash", "-c", command]
    return args

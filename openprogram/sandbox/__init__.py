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

import contextvars
import functools
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass, replace

log = logging.getLogger(__name__)

MODE_DANGER_FULL_ACCESS = "danger-full-access"
MODE_WORKSPACE_WRITE = "workspace-write"
MODES = (MODE_DANGER_FULL_ACCESS, MODE_WORKSPACE_WRITE)

UNAVAILABLE_POLICY_REFUSE = "refuse"
UNAVAILABLE_POLICY_WARN = "warn"
UNAVAILABLE_POLICY = (UNAVAILABLE_POLICY_REFUSE, UNAVAILABLE_POLICY_WARN)

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
# ``_applications_dir``), which no config can remove. Git hooks and git config
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

# Node needs hardware sysctls during startup; uname-based Python tooling
# needs these four stable kernel names.  A blanket sysctl grant also exposes
# unrelated kernel state, while a blanket mach-lookup grant makes services
# such as the pasteboard reachable.  Common CLI tooling does not need Mach
# service discovery.
_MACOS_SYSCTL_NAMES = (
    "kern.hostname",
    "kern.osrelease",
    "kern.ostype",
    "kern.version",
)


class SandboxUnavailable(RuntimeError):
    """Config asks for a sandbox the platform cannot provide."""


@dataclass(frozen=True)
class SandboxPolicy:
    """One resolved policy. ``writable_roots`` is *extra* — the working
    directory is always writable. ``allow_read`` re-opens a named path
    inside a wider deny (narrower wins; equally-specific deny wins)."""
    writable_roots: tuple[str, ...] = ()
    deny_read: tuple[str, ...] = DEFAULT_DENY_READ
    deny_write: tuple[str, ...] = DEFAULT_DENY_WRITE
    network: bool = False
    pass_env: tuple[str, ...] = ()
    allow_read: tuple[str, ...] = ()
    # Escalated executions may inspect host processes (ps / lsof / top).
    # Default sandboxed runs stay same-sandbox only.
    host_process_info: bool = False


_NO_PROCESS_POLICY = object()
_process_policy_override: object | SandboxPolicy | None = _NO_PROCESS_POLICY
_execution_policy_override: contextvars.ContextVar[
    object | SandboxPolicy | None
] = contextvars.ContextVar(
    "openprogram_sandbox_execution_policy", default=_NO_PROCESS_POLICY,
)


# --- availability ----------------------------------------------------------

def unavailable_reason() -> str | None:
    """Why the sandbox cannot run here, or None when it can."""
    if sys.platform == "darwin":
        if os.path.exists("/usr/bin/sandbox-exec"):
            return None
        return "macOS needs /usr/bin/sandbox-exec"
    if sys.platform.startswith("linux"):
        executable = shutil.which("bwrap")
        if not executable:
            return "Linux needs bubblewrap (install the `bubblewrap` package)"
        return _bwrap_unavailable_reason(executable)
    return f"no sandbox backend for platform {sys.platform!r}"


@functools.cache
def _bwrap_unavailable_reason(executable: str) -> str | None:
    """Probe the namespaces required by our policy, once per bwrap binary."""
    try:
        proc = subprocess.run(
            [
                executable,
                "--new-session",
                "--die-with-parent",
                "--unshare-pid",
                "--unshare-ipc",
                "--unshare-uts",
                "--unshare-net",
                "--cap-drop", "ALL",
                "--ro-bind", "/", "/",
                "--proc", "/proc",
                "--dev", "/dev",
                "--", "/bin/true",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            env={},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"Linux bubblewrap probe failed: {exc}"
    if proc.returncode == 0:
        return None
    lines = (proc.stderr or proc.stdout).strip().splitlines()
    detail = f": {lines[-1]}" if lines else ""
    return "Linux bubblewrap cannot create the required namespaces" + detail


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
    applications = _applications_dir()
    if applications in policy.deny_write:
        return policy
    return replace(policy, deny_write=policy.deny_write + (applications,))


def resolve_policy(*, required: bool = False) -> SandboxPolicy | None:
    """The policy configured right now, or None when the sandbox is off.

    Read per command, so a toggle takes effect on the next command in
    every process rather than only in the context that flipped it.
    """
    execution_override = _execution_policy_override.get()
    if execution_override is not _NO_PROCESS_POLICY:
        if execution_override is None:
            return _with_hard_floor(SandboxPolicy()) if required else None
        return execution_override  # type: ignore[return-value]
    if _process_policy_override is not _NO_PROCESS_POLICY:
        if _process_policy_override is None:
            return _with_hard_floor(SandboxPolicy()) if required else None
        return _process_policy_override  # type: ignore[return-value]
    sb = _config_section()
    if (str(sb.get("mode") or MODE_WORKSPACE_WRITE).strip().lower()
            != MODE_WORKSPACE_WRITE
            and not required):
        return None
    deny_r = sb.get("deny_read")
    deny_w = sb.get("deny_write")
    allow_r = sb.get("allow_read")
    return _with_hard_floor(SandboxPolicy(
        writable_roots=tuple(sb.get("writable_roots") or ()),
        deny_read=tuple(deny_r) if isinstance(deny_r, list) else DEFAULT_DENY_READ,
        deny_write=(tuple(deny_w) if isinstance(deny_w, list)
                    else DEFAULT_DENY_WRITE),
        network=bool(sb.get("network") or False),
        pass_env=tuple(sb.get("pass_env") or ()),
        allow_read=tuple(allow_r) if isinstance(allow_r, list) else (),
    ))


@contextmanager
def escalated_policy():
    """Relax configurable restrictions for one approved execution.

    The OS sandbox remains active solely to enforce the non-configurable
    applications write prohibition. Credentials also stay out of the child
    environment; approval changes execution reach, not secret handling.
    """
    policy = _with_hard_floor(SandboxPolicy(
        writable_roots=("/",),
        deny_read=(),
        deny_write=(),
        network=True,
        pass_env=(),
        host_process_info=True,
    ))
    token = _execution_policy_override.set(policy)
    try:
        yield
    finally:
        _execution_policy_override.reset(token)


def is_sandbox_denial(
    exit_code: int, stdout: str, stderr: str, *, sandboxed: bool,
) -> bool:
    """True when a sandboxed command most likely failed on the sandbox.

    A heuristic on the child's own output, not a verdict from the
    sandbox: no backend reports "I denied this". A command that prints
    one of these strings for its own reasons reads as a false positive,
    and a backend phrasing a denial differently reads as a miss. Use it
    to word an error message, never to decide whether access was
    granted.
    """
    if not sandboxed or exit_code == 0:
        return False
    text = f"{stdout}\n{stderr}".lower()
    return any(marker in text for marker in (
        "operation not permitted",
        "permission denied",
        "read-only file system",
        "sandbox-exec:",
        "bwrap:",
    ))


def policy_to_dict(policy: SandboxPolicy) -> dict:
    policy = _with_hard_floor(policy)
    return {
        "writable_roots": list(policy.writable_roots),
        "deny_read": list(policy.deny_read),
        "deny_write": list(policy.deny_write),
        "network": policy.network,
        "pass_env": list(policy.pass_env),
        "allow_read": list(policy.allow_read),
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
        allow_read=_strings("allow_read"),
    ))


def policy_hash(policy: SandboxPolicy) -> str:
    payload = json.dumps(
        policy_to_dict(policy), sort_keys=True, separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def policy_snapshot() -> dict:
    """Serializable effective policy for a fresh subprocess."""
    policy = resolve_policy()
    return {
        "enabled": policy is not None,
        "policy": policy_to_dict(policy) if policy is not None else None,
    }


def install_policy_snapshot(snapshot: dict) -> None:
    """Pin this process to the parent's serialized sandbox decision."""
    global _process_policy_override
    if not isinstance(snapshot, dict) or not isinstance(
        snapshot.get("enabled"), bool
    ):
        raise ValueError("sandbox policy snapshot is invalid")
    if snapshot["enabled"]:
        _process_policy_override = policy_from_dict(snapshot.get("policy"))
    else:
        _process_policy_override = None


def _applications_dir() -> str:
    """Absolute glob for the directory the function watcher auto-imports.
    Resolved rather than configured — it moves with the installation, and
    a user editing the deny list must not be able to drop it."""
    return os.path.join(_applications_root(), "**")


def _applications_root() -> str:
    from openprogram.protected_paths import applications_root
    return applications_root()


def apply_in_bypass() -> bool:
    """Whether the configurable sandbox limits still apply under bypass.

    Default False: bypass means full access, matching Claude Code's
    ``--dangerously-skip-permissions``. Opt in via ``sandbox.apply_in_bypass``
    to keep deny_read/deny_write/network enforcement even in bypass mode.
    """
    return bool(_config_section().get("apply_in_bypass") or False)


def unavailable_policy() -> str:
    v = str(_config_section().get("unavailable_policy") or UNAVAILABLE_POLICY_REFUSE)
    return v if v in UNAVAILABLE_POLICY else UNAVAILABLE_POLICY_REFUSE


def set_mode(enabled: bool) -> None:
    """Persist the on/off state. Used by the ``/sandbox`` toggles."""
    from openprogram.config_schema import set_setting
    set_setting("sandbox.mode", MODE_WORKSPACE_WRITE if enabled else MODE_DANGER_FULL_ACCESS)


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


SANDBOX_DENIAL_GUIDANCE = (
    "request sandbox escalation or ask the owner to change sandbox.deny_read; "
    "do not relocate or copy the protected content"
)


def _hard_floor_read_globs() -> tuple[str, ...]:
    from openprogram.paths import get_state_dir
    globs = [str(get_state_dir() / "auth") + "/**", _applications_dir()]
    documented = os.path.expanduser("~/.openprogram/auth") + "/**"
    if documented not in globs:
        globs.append(documented)
    return tuple(globs)


def is_hard_floor_read(path: str) -> bool:
    """True for ~/.openprogram/auth/** and the agentics directory."""
    target = os.path.realpath(os.path.expanduser(os.fspath(path)))
    return any(re.match(rx, target) for rx in _regexes_for(_hard_floor_read_globs()))


def _pattern_specificity(pattern: str) -> int:
    return len(os.path.expanduser(_static_prefix(pattern.strip())).rstrip("/"))


def _matching_patterns(target: str, patterns: tuple[str, ...]) -> list[str]:
    hit: list[str] = []
    for pattern in patterns:
        if any(re.match(rx, target) for rx in _regexes_for((pattern,))):
            hit.append(pattern)
    return hit


def read_is_denied(target: str, policy: SandboxPolicy) -> bool:
    """Whether a realpath'd *target* is a denied read under *policy*.

    Narrower allow_read re-opens a wider deny; equally-specific deny wins.
    Hard-floor paths ignore allow_read.
    """
    denies = _matching_patterns(target, policy.deny_read)
    if not denies:
        return False
    if is_hard_floor_read(target):
        return True
    allows = _matching_patterns(target, policy.allow_read)
    if not allows:
        return True
    return max(map(_pattern_specificity, allows)) <= max(
        map(_pattern_specificity, denies)
    )


def match_deny_read(
    text: str, policy: SandboxPolicy | None = None,
) -> tuple[str, str] | None:
    """Best-effort (path, deny glob) from command/error text, or None."""
    if policy is None:
        policy = resolve_policy()
    if policy is None or not text or not policy.deny_read:
        return None
    tokens: list[str] = []
    for raw in re.findall(r"[^\s'\"`:;]+", text):
        tok = raw.strip(".,)(")
        if tok.startswith(("~", "/", "./", "../", ".")) or "/" in tok:
            tokens.append(tok)
    for tok in tokens:
        try:
            real = os.path.realpath(os.path.expanduser(tok))
        except Exception:
            real = tok
        hits = _matching_patterns(real, policy.deny_read) or _matching_patterns(
            tok, policy.deny_read
        )
        if hits:
            return real, max(hits, key=_pattern_specificity)
    return None


def named_denial_text(path: str | None = None, rule: str | None = None) -> str:
    if path and rule:
        head = f"sandbox denied read of {path} (matched deny glob {rule})."
    else:
        head = "sandbox denied this read."
    return f"{head} {SANDBOX_DENIAL_GUIDANCE}"


def persist_allow_read(path: str | None) -> str | None:
    """Append *path* to ``sandbox.allow_read``. None on success, else error."""
    if not path or not isinstance(path, str) or not path.strip():
        return "always_path requires a concrete blocked path"
    if is_hard_floor_read(path):
        return (
            f"{path} is a non-configurable hard floor "
            "(~/.openprogram/auth or the agentics directory) "
            "and cannot be added to sandbox.allow_read"
        )
    from openprogram.config_schema import set_setting
    from openprogram.setup import _read_config
    real = os.path.realpath(os.path.expanduser(path.strip()))
    current = list((_read_config().get("sandbox") or {}).get("allow_read") or [])
    if real not in current:
        current.append(real)
    result = set_setting("sandbox.allow_read", current)
    if result.get("error"):
        return str(result["error"])
    return None


def validate_write_path(path, *, cwd: str | None = None) -> str | None:
    """Return a sandbox-policy violation for a direct file write, if any."""
    target = os.path.realpath(os.path.expanduser(os.fspath(path)))
    applications = os.path.realpath(_applications_root())
    target_key = target.casefold()
    applications_key = applications.casefold()
    if target_key == applications_key or target_key.startswith(
        applications_key + os.sep
    ):
        return "writes to auto-imported application Python are forbidden"
    from openprogram.protected_paths import program_sources_path
    if target_key == os.path.realpath(program_sources_path()).casefold():
        return "writes to the agentic source registry are forbidden"

    policy = resolve_policy()
    if policy is None:
        return None
    if cwd is None:
        try:
            from openprogram.worktree.context import current_worktree_path
            cwd = current_worktree_path()
        except Exception:
            cwd = None
    base = os.path.realpath(cwd or os.getcwd())
    roots = _writable_roots(base, policy)
    if not any(target == root or target.startswith(root + os.sep) for root in roots):
        return f"path is outside writable roots: {target}"
    for pattern in policy.deny_write:
        regex = _glob_to_regex(pattern)
        if regex and re.match(regex, target):
            return f"path is denied by sandbox policy: {target}"
    return None


def validate_read_path(path) -> str | None:
    """Return a sandbox-policy violation for a direct file read, if any.

    The OS sandbox enforces ``deny_read`` for commands it wraps, but the
    in-process read tools (read / grep / glob / list) never go through a
    child process — they open the file in the host interpreter, where no
    Seatbelt or bwrap rule applies. This is that missing enforcement
    point. No active policy (danger-full-access, or a plain library use)
    means no restriction, exactly as for writes.
    """
    matcher = read_denier()
    if matcher is None:
        return None
    target = os.path.realpath(os.path.expanduser(os.fspath(path)))
    if not matcher(target):
        return None
    policy = resolve_policy()
    hits = _matching_patterns(target, policy.deny_read) if policy else []
    rule = max(hits, key=_pattern_specificity) if hits else None
    return named_denial_text(target, rule)


def read_denier():
    """A ``path -> bool`` deny test for the active policy, or None.

    Returned once and reused across a directory walk: ``validate_read_path``
    per file would recompile every deny regex per candidate. Takes a real
    path (already resolved) and answers whether reading it is denied.
    """
    policy = resolve_policy()
    if policy is None or not policy.deny_read:
        return None
    deny_items = [
        (p, [re.compile(rx) for rx in _regexes_for((p,))])
        for p in policy.deny_read
    ]
    if not any(rxs for _, rxs in deny_items):
        return None
    allow_items = [
        (p, [re.compile(rx) for rx in _regexes_for((p,))])
        for p in policy.allow_read
    ]
    floor_rx = [re.compile(rx) for rx in _regexes_for(_hard_floor_read_globs())]

    def _hits(items, target: str) -> list[str]:
        return [p for p, rxs in items if any(rx.match(target) for rx in rxs)]

    def _denied(target: str) -> bool:
        real = os.path.realpath(target)
        denies = _hits(deny_items, real)
        if not denies:
            return False
        if any(rx.match(real) for rx in floor_rx):
            return True
        allows = _hits(allow_items, real)
        if not allows:
            return True
        return max(map(_pattern_specificity, allows)) <= max(
            map(_pattern_specificity, denies)
        )

    return _denied


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
        # Escalated policy re-opens host process INSPECTION (ps/lsof);
        # signalling stays same-sandbox in every mode.
        ("(allow process-info*)" if policy.host_process_info
         else "(allow process-info* (target same-sandbox))"),
        "(allow signal (target same-sandbox))",
        "(allow ipc-posix-sem)",   # python multiprocessing
        "(allow ipc-posix-shm)",
        "(allow sysctl-read",
        '  (sysctl-name-prefix "hw.")',
        *(f'  (sysctl-name "{name}")' for name in _MACOS_SYSCTL_NAMES),
        ")",
        '(allow file-read* (subpath "/"))',
    ]
    for root in _writable_roots(cwd, policy):
        lines.append(f"(allow file-write* (subpath {_sbpl_str(root)}))")
    tmpdir = os.path.realpath(os.environ.get("TMPDIR") or "/tmp")
    lines += [
        f"(allow file-write* (subpath {_sbpl_str(tmpdir)}))",
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
    # SBPL last-match-wins: re-open only allow_read paths that beat deny.
    for path in _concrete_paths(policy.allow_read):
        if not read_is_denied(path, policy):
            lines.append(f'(allow file-read* (subpath {_sbpl_str(path)}))')
    return "\n".join(lines) + "\n"


def _bwrap_args(command: str, cwd: str, policy: SandboxPolicy) -> list[str]:
    args = [
        "bwrap",
        "--new-session",       # bubblewrap documents this as the TIOCSTI guard
        "--die-with-parent",
        "--unshare-ipc",
        "--unshare-uts",
        "--cap-drop", "ALL",
        "--ro-bind", "/", "/",
        "--proc", "/proc",
        "--dev", "/dev",
    ]
    if not policy.host_process_info:
        # else /proc/<pid>/environ of host processes is readable and any
        # same-uid process is killable. Escalated policy trades that for
        # working ps/lsof — consistent with "hard-constraints-only".
        args.insert(3, "--unshare-pid")
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
        if not read_is_denied(path, policy):
            continue
        if os.path.isdir(path):
            args += ["--perms", "0000", "--tmpfs", path]
        elif os.path.exists(path):
            args += ["--ro-bind", "/dev/null", path]
    for path in _concrete_paths(policy.allow_read):
        if read_is_denied(path, policy) or not os.path.exists(path):
            continue
        args += ["--ro-bind", path, path]
    for path in _concrete_paths(policy.deny_write):
        if os.path.exists(path):
            args += ["--ro-bind", path, path]
    args += ["--", "/bin/bash", "-c", command]
    return args

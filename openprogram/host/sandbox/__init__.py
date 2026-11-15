"""System-level sandbox — restrict a shell command's file, process and
network access.

macOS: sandbox-exec (Seatbelt). Linux: bubblewrap (bwrap). Windows delegates
the same bubblewrap policy to the default WSL2 distribution; it never rewrites
Windows ACLs.

The policy is resolved from ``~/.openprogram/config.json`` (the
``sandbox.*`` keys in ``state.settings.SETTINGS``) at the moment a command
is wrapped. That matters more than it sounds: the switch used to live on
a ``ContextVar`` and was lost at every boundary that starts a fresh
context — the web UI's asyncio task handing work to a bare thread, the
``spawn`` subprocess behind ``Agent method``, and any nested CLI.
A file every process reads cannot be lost at a boundary, and cannot be
skipped by an approval-layer bypass either, because it is read below the
approval layer. Callers that already hold a policy pass it explicitly to
``wrap_command``.

What the boundary is for every available platform backend:

* writes are confined to the working directory plus configured roots
* inside them, files that something outside the sandbox runs later (shell
  startup files, the repository's git hooks and config, OpenProgram's own
  state) stay read-only; see ``protected_writes``
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
from dataclasses import dataclass

from openprogram.host._platform import environment as platform_environment
from openprogram.host.sandbox import protected_writes

log = logging.getLogger(__name__)

MODE_DANGER_FULL_ACCESS = "danger-full-access"
MODE_WORKSPACE_WRITE = "workspace-write"
MODE_AUTO = "auto"
# The default: no sandbox unless a conversation turns its Sandbox switch
# on (the composer's Plus menu), which then uses the backend when it is
# available.
MODE_OFF = "off"
MODES = (MODE_OFF, MODE_AUTO, MODE_DANGER_FULL_ACCESS, MODE_WORKSPACE_WRITE)

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

# Extra write denials a user configures. Empty by default: the escapes of
# this shape (code that runs outside the sandbox later) are covered by
# ``protected_writes``, which no config can remove. Git hooks and config are
# covered only for the repository the command starts in, because `git init` and
# `git clone` write `.git/hooks/` of a new one.
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


class SandboxPathDenied(PermissionError):
    """A path the conversation's sandbox does not reach; the layers above word the refusal."""

    def __init__(self, violation: str):
        super().__init__(violation)
        self.violation = violation


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
    network_domains: tuple[tuple[str, str], ...] | None = None
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
        return bubblewrap_unavailable_reason()
    if sys.platform == "win32":
        reason = platform_environment.windows_wsl_sandbox_reason()
        if reason is None:
            return None
        # Shown on the Sandbox switch and by doctor: what is missing, and how to add it.
        return (f"Sandbox unavailable: needs WSL2 ({reason}). Install it with `wsl --install`, "
                "then install bubblewrap in the default distribution")
    return f"no sandbox backend for platform {sys.platform!r}"


def bubblewrap_unavailable_reason() -> str | None:
    """Why bubblewrap cannot confine a process here, from really running it."""
    executable = shutil.which("bwrap")
    if not executable:
        return "Linux needs bubblewrap (install the `bubblewrap` package)"
    return _bwrap_unavailable_reason(executable)


def _apparmor_restricts_namespaces() -> bool:
    try:
        with open("/proc/sys/kernel/apparmor_restrict_unprivileged_userns", encoding="ascii") as stream:
            return stream.read().strip() == "1"
    except OSError:
        return False


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
    remedy = ""
    if _apparmor_restricts_namespaces():
        remedy = (". AppArmor restricts these namespaces on this system, as Ubuntu 24.04 does by default; "
                  "to allow them, run `sudo sysctl kernel.apparmor_restrict_unprivileged_userns=0`")
    return "Linux bubblewrap cannot create the required namespaces" + detail + remedy


def is_available() -> bool:
    return unavailable_reason() is None


# --- policy resolution -----------------------------------------------------

def _config_section() -> dict:
    try:
        from openprogram.state.config import read_config
        return (read_config().get("sandbox") or {})
    except Exception:  # noqa: BLE001 — a broken config must not break bash
        return {}


def _config_mode_on(sb: dict) -> bool:
    mode = str(sb.get("mode") or MODE_OFF).strip().lower()
    if mode == MODE_AUTO:
        return unavailable_reason() is None
    return mode == MODE_WORKSPACE_WRITE


def _policy_from_section(sb: dict) -> SandboxPolicy:
    from .network_policy import parse_domains
    deny_r = sb.get("deny_read")
    deny_w = sb.get("deny_write")
    allow_r = sb.get("allow_read")
    return SandboxPolicy(
        writable_roots=tuple(sb.get("writable_roots") or ()),
        deny_read=tuple(deny_r) if isinstance(deny_r, list) else DEFAULT_DENY_READ,
        deny_write=(tuple(deny_w) if isinstance(deny_w, list)
                    else DEFAULT_DENY_WRITE),
        network=bool(sb.get("network") or False),
        network_domains=parse_domains(sb.get("network_domains")),
        pass_env=tuple(sb.get("pass_env") or ()),
        allow_read=tuple(allow_r) if isinstance(allow_r, list) else (),
    )


def _resolve_configured_policy(
    session_enabled: bool | None, *, required: bool,
) -> SandboxPolicy | None:
    """Session override → project/global ``sandbox.mode`` → system default.

    ``session_enabled`` is the explicit session switch (None = inherit).
    The system default is off: without a switch or a configured mode a
    local command runs unsandboxed.
    This path ignores turn/process snapshots so a UI read during a turn
    still describes the next turn, not the frozen one.
    """
    if session_enabled is False and not required:
        return None
    sb = _config_section()
    if session_enabled is not True and not _config_mode_on(sb) and not required:
        if not (sb.get("mode") == MODE_AUTO and sb.get("network_domains") is not None):
            return None
    return _policy_from_section(sb)


def resolve_policy(
    *, required: bool = False, session_enabled: bool | None = None,
) -> SandboxPolicy | None:
    """The policy in force right now, or None when the sandbox is off.

    A turn snapshot (``_execution_policy_override``) or a subprocess pin
    (``_process_policy_override``) wins. Otherwise session override, then
    configured ``sandbox.mode``, then the system default.
    """
    execution_override = _execution_policy_override.get()
    if execution_override is not _NO_PROCESS_POLICY:
        if execution_override is None:
            return SandboxPolicy() if required else None
        return execution_override  # type: ignore[return-value]
    if _process_policy_override is not _NO_PROCESS_POLICY:
        if _process_policy_override is None:
            return SandboxPolicy() if required else None
        return _process_policy_override  # type: ignore[return-value]
    return _resolve_configured_policy(session_enabled, required=required)


def bind_turn_policy(session_enabled: bool | None = None):
    """Freeze the effective policy for this turn. Nested calls inherit."""
    if _execution_policy_override.get() is not _NO_PROCESS_POLICY:
        return None
    return _execution_policy_override.set(
        resolve_policy(session_enabled=session_enabled),
    )


def reset_turn_policy(token) -> None:
    if token is not None:
        _execution_policy_override.reset(token)


@contextmanager
def turn_policy(session_enabled: bool | None = None):
    """Bind :func:`bind_turn_policy` for a block. Nested calls inherit."""
    token = bind_turn_policy(session_enabled)
    try:
        yield
    finally:
        reset_turn_policy(token)


def ui_state(session_enabled: bool | None = None) -> dict:
    """Session switch + backend availability for the Plus menu."""
    reason = unavailable_reason()
    return {
        "sandbox_enabled": session_enabled,
        "sandbox": _resolve_configured_policy(
            session_enabled, required=False,
        ) is not None,
        "sandbox_available": reason is None,
        "sandbox_unavailable_reason": reason,
    }


@contextmanager
def without_policy():
    """Run a block with no sandbox, for work that uses one only where it can."""
    token = _execution_policy_override.set(None)
    try:
        yield
    finally:
        _execution_policy_override.reset(token)


@contextmanager
def escalated_policy():
    """Relax configurable restrictions for one approved execution.

    Credentials stay out of the child environment: leaving the sandbox
    changes what a command can reach, not how secrets are handled.
    """
    policy = SandboxPolicy(
        writable_roots=("/",),
        deny_read=(),
        deny_write=(),
        network=True,
        pass_env=(),
        host_process_info=True,
    )
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
    )) or is_network_denial(exit_code, stdout, stderr, sandboxed=sandboxed)


# What a client prints when the sandbox has taken the network away: name
# resolution fails first, so DNS errors dominate. Same heuristic caveats as
# ``is_sandbox_denial``.
_NETWORK_MARKERS = (
    "could not resolve host",
    "could not resolve hostname",
    "nodename nor servname provided",
    "temporary failure in name resolution",
    "name or service not known",
    "name resolution failed",
    "getaddrinfo enotfound",
    "getaddrinfo eai_again",
    "network is unreachable",
    "no route to host",
    "failed to establish a new connection",
)


def is_network_denial(
    exit_code: int, stdout: str, stderr: str, *, sandboxed: bool,
    policy: SandboxPolicy | None = None,
) -> bool:
    """True when a sandboxed command most likely failed for lack of network."""
    if not sandboxed or exit_code == 0:
        return False
    if policy is None:
        policy = resolve_policy()
    if policy is None or policy.network:
        return False
    text = f"{stdout}\n{stderr}".lower()
    return any(marker in text for marker in _NETWORK_MARKERS)


def policy_to_dict(policy: SandboxPolicy) -> dict:
    return {
        "writable_roots": list(policy.writable_roots),
        "deny_read": list(policy.deny_read),
        "deny_write": list(policy.deny_write),
        "network": policy.network,
        **({"network_domains": dict(policy.network_domains)} if policy.network_domains is not None else {}),
        "pass_env": list(policy.pass_env),
        "allow_read": list(policy.allow_read),
    }


def policy_from_dict(data: dict) -> SandboxPolicy:
    from .network_policy import parse_domains
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
    return SandboxPolicy(
        writable_roots=_strings("writable_roots"),
        deny_read=_strings("deny_read"),
        deny_write=_strings("deny_write"),
        network=network,
        network_domains=parse_domains(data.get("network_domains")),
        pass_env=_strings("pass_env"),
        allow_read=_strings("allow_read"),
    )


def policy_hash(policy: SandboxPolicy) -> str:
    return policy_dict_hash(policy_to_dict(policy))


def policy_dict_hash(data: dict) -> str:
    """Hash of a serialized policy exactly as it was written.

    Reading a policy back adds this installation's hard floor, which names
    its own package directory; a policy written by another installation (a
    CLI next to the App's worker) must still hash to what its writer
    recorded.
    """
    payload = json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
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


def default_unavailable_policy() -> str:
    """``refuse``, except on Windows, where the sandbox needs WSL2.

    Most Windows machines have no WSL2 distribution with bubblewrap. Refusing
    there would turn the Sandbox switch into "every command fails"; the
    command runs in the host shell instead, and the switch's hint and
    ``openprogram doctor`` say the sandbox needs WSL2.
    """
    return UNAVAILABLE_POLICY_WARN if sys.platform == "win32" else UNAVAILABLE_POLICY_REFUSE


def unavailable_policy() -> str:
    default = default_unavailable_policy()
    v = str(_config_section().get("unavailable_policy") or default)
    return v if v in UNAVAILABLE_POLICY else default


def set_mode(enabled: bool) -> None:
    """Persist the on/off state. Used by the ``/sandbox`` toggles."""
    from openprogram.state.settings import set_setting
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
    if pattern.strip().startswith("~/"):
        p = os.path.join(os.path.realpath(os.path.expanduser("~")), pattern.strip()[2:])
    if os.sep == "\\":
        p = p.replace("\\", "/")
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


def _regex_path(path: str) -> str:
    """Use one separator convention for Windows glob matching."""

    value = os.fspath(path)
    return value.replace("\\", "/") if os.sep == "\\" else value


# The escalation is requested before the command runs: the approval has to be
# published at the Agent safe point, ahead of any effect.
ESCALATION_HINT = (
    'run it again through bash with sandbox_permissions="require_escalated" '
    "and a one-sentence justification to run it outside the sandbox"
)

SANDBOX_DENIAL_GUIDANCE = (
    f"To proceed, {ESCALATION_HINT}, or ask the owner to change "
    "sandbox.deny_read; do not relocate or copy the protected content"
)

NETWORK_DENIAL_GUIDANCE = (
    f"To proceed, {ESCALATION_HINT}, or ask the owner to turn on "
    "sandbox.network; retrying it unchanged fails the same way"
)


def _hard_floor_read_globs() -> tuple[str, ...]:
    from openprogram.state.paths import get_state_dir
    globs = [str(get_state_dir() / "auth") + "/**"]
    documented = os.path.expanduser("~/.openprogram/auth") + "/**"
    if documented not in globs:
        globs.append(documented)
    return tuple(globs)


def is_hard_floor_read(path: str) -> bool:
    """True for ~/.openprogram/auth/**, which holds credentials."""
    target = _regex_path(os.path.realpath(os.path.expanduser(os.fspath(path))))
    return any(re.match(rx, target) for rx in _regexes_for(_hard_floor_read_globs()))


def _pattern_specificity(pattern: str) -> int:
    return len(os.path.expanduser(_static_prefix(pattern.strip())).rstrip("/"))


def _matching_patterns(target: str, patterns: tuple[str, ...]) -> list[str]:
    target = _regex_path(target)
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
    for raw in re.findall(r"[^\s'\"`;]+", text):
        # Keep the colon inside ``C:\path`` while trimming punctuation such
        # as the colon after ``cat:``. Windows diagnostics use backslashes,
        # and the old POSIX-only tokenizer silently lost their denied path.
        tok = raw.strip(".,)(:")
        if (
            tok.startswith(("~", "/", "./", "../", "."))
            or "/" in tok
            or "\\" in tok
        ):
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
        head = "sandbox denied this access."
    return f"{head} {SANDBOX_DENIAL_GUIDANCE}"


def network_denial_text() -> str:
    return ("sandbox blocked network access for this command "
            f"(sandbox.network is off). {NETWORK_DENIAL_GUIDANCE}")


def persist_allow_read(path: str | None) -> str | None:
    """Append *path* to ``sandbox.allow_read``. None on success, else error."""
    if not path or not isinstance(path, str) or not path.strip():
        return "always_path requires a concrete blocked path"
    if is_hard_floor_read(path):
        return (
            f"{path} is a non-configurable hard floor "
            "(~/.openprogram/auth) "
            "and cannot be added to sandbox.allow_read"
        )
    from openprogram.state.settings import set_setting
    from openprogram.state.config import read_config
    real = os.path.realpath(os.path.expanduser(path.strip()))
    current = list((read_config().get("sandbox") or {}).get("allow_read") or [])
    if real not in current:
        current.append(real)
    result = set_setting("sandbox.allow_read", current)
    if result.get("error"):
        return str(result["error"])
    return None


def validate_write_path(path, *, cwd: str | None = None) -> str | None:
    """Return a sandbox-policy violation for a direct file write, if any."""
    target = os.path.realpath(os.path.expanduser(os.fspath(path)))
    policy = resolve_policy()
    if policy is None:
        return None
    if cwd is None:
        try:
            from openprogram.state.worktree.context import current_worktree_path
            cwd = current_worktree_path()
        except Exception:
            cwd = None
    base = os.path.realpath(cwd or os.getcwd())
    roots = _writable_roots(base, policy)
    if not any(root == "/" or target == root or target.startswith(root.rstrip(os.sep) + os.sep) for root in roots):
        return f"path is outside writable roots: {target}"
    for pattern in policy.deny_write:
        regex = _glob_to_regex(pattern)
        if regex and re.match(regex, _regex_path(target)):
            return f"path is denied by sandbox policy: {target}"
    if not protected_writes.lifted(policy.writable_roots):
        reason = protected_writes.denial(target, base, roots)
        if reason:
            return reason
    return None


def validate_read_path(path) -> str | None:
    """Return a sandbox-policy violation for a direct file read, if any.

    The OS sandbox enforces ``deny_read`` for commands it wraps, but the
    in-process read tools (read / grep / glob / list) never go through a
    child process — they open the file in the host interpreter, where no
    Seatbelt or bwrap rule applies. This is that missing enforcement
    point. No active policy (danger-full-access, or a plain library use)
    means no configurable restriction. Verifier-internal state stays private
    even without a sandbox policy.
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
    from openprogram.host import runtime_facts
    from openprogram.state.paths import get_state_dir
    verifier_root = (
        os.path.realpath(get_state_dir() / "self-updates")
        if runtime_facts.turn_request_source() == "self_update_verify" else None
    )
    def verifier_denied(target: str) -> bool:
        real = os.path.realpath(target)
        return verifier_root is not None and (real == verifier_root or real.startswith(verifier_root + os.sep))

    policy = resolve_policy()
    if policy is None or not policy.deny_read:
        return verifier_denied if verifier_root is not None else None
    deny_items = [
        (p, [re.compile(rx) for rx in _regexes_for((p,))])
        for p in policy.deny_read
    ]
    if not any(rxs for _, rxs in deny_items):
        return verifier_denied if verifier_root is not None else None
    allow_items = [
        (p, [re.compile(rx) for rx in _regexes_for((p,))])
        for p in policy.allow_read
    ]
    floor_rx = [re.compile(rx) for rx in _regexes_for(_hard_floor_read_globs())]

    def _hits(items, target: str) -> list[str]:
        normalized = _regex_path(target)
        return [p for p, rxs in items if any(rx.match(normalized) for rx in rxs)]

    def _denied(target: str) -> bool:
        real = os.path.realpath(target)
        if verifier_denied(real):
            return True
        denies = _hits(deny_items, real)
        if not denies:
            return False
        if any(rx.match(_regex_path(real)) for rx in floor_rx):
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
        if os.path.isabs(prefix):
            real = os.path.realpath(prefix)
            if real != prefix.rstrip("/\\"):
                suffix = os.path.expanduser(pattern)[len(prefix):]
                separator = os.sep if prefix.endswith(("/", "\\")) and suffix else ""
                variants.append(real + separator + suffix)
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
                 policy: SandboxPolicy | None = None, *,
                 private_tmp: bool = False,
                 allow_subprocesses: bool = True,
                 read_only_roots: tuple[str, ...] = ()) -> tuple[list[str], bool]:
    """Wrap *command* in a sandbox invocation. Returns ``(args, shell)``."""
    if policy is None:
        policy = SandboxPolicy()
    cwd = os.path.realpath(cwd)
    if policy.network and policy.network_domains is not None:
        if sys.platform != "darwin":
            raise SandboxUnavailable("network domain restrictions currently require macOS")
        payload = dict(command=command, cwd=cwd, policy=policy_to_dict(policy),
                       private_tmp=private_tmp, allow_subprocesses=allow_subprocesses,
                       host_process_info=policy.host_process_info)
        return ([sys.executable, "-I", "-m", "openprogram.host.sandbox.network_runner", json.dumps(payload)], False)
    if sys.platform == "darwin":
        profile = _seatbelt_profile(cwd, policy, private_tmp=private_tmp,
                                    allow_subprocesses=allow_subprocesses)
        return (["/usr/bin/sandbox-exec", "-p", profile,
                 "/bin/bash", "-c", command], False)
    if sys.platform == "win32":
        return (_wsl_bwrap_args(command, cwd, policy), False)
    return (_bwrap_args(command, cwd, policy, read_only_roots=read_only_roots), False)


def _writable_roots(cwd: str, policy: SandboxPolicy) -> list[str]:
    roots = [cwd]
    for r in (*policy.writable_roots, *_linked_worktree_git_roots(cwd)):
        # An approved escalation's root is "/": every path, on every drive.
        real = "/" if r == "/" else os.path.realpath(os.path.expanduser(r))
        if real not in roots:
            roots.append(real)
    return roots


# Branches OpenProgram creates for its worktrees (see worktree.manager).
_WORKTREE_REF_NAMESPACE = ("refs", "heads", "op", "wt")


def _linked_worktree_git_roots(cwd: str) -> tuple[str, ...]:
    """Git state a linked worktree at ``cwd`` must write to commit and rebase.

    A linked worktree keeps its index and HEAD in ``<common>/worktrees/<id>``
    and shares objects and refs with the source repository, all outside the
    worktree folder. Only that worktree's own admin directory, the object
    store and the ``op/wt`` branch namespace become writable: the source
    repository's config, hooks and other branches (``main``) stay read-only.
    """
    current = cwd
    while True:
        marker = os.path.join(current, ".git")
        if os.path.lexists(marker):
            break
        parent = os.path.dirname(current)
        if parent == current:
            return ()
        current = parent
    try:
        if not os.path.isfile(marker) or os.path.islink(marker):
            return ()  # A main checkout already lives under its own root.
        with open(marker, encoding="utf-8") as stream:
            line = stream.read(4096).strip()
        if not line.startswith("gitdir: "):
            return ()
        admin = os.path.realpath(os.path.join(current, line[len("gitdir: "):]))
        with open(os.path.join(admin, "commondir"), encoding="utf-8") as stream:
            common = os.path.realpath(os.path.join(admin, stream.read(4096).strip()))
    except (OSError, UnicodeDecodeError):
        return ()
    if os.path.dirname(admin) != os.path.join(common, "worktrees"):
        return ()
    candidates = (
        admin,
        os.path.join(common, "objects"),
        os.path.join(common, *_WORKTREE_REF_NAMESPACE),
        os.path.join(common, "logs", *_WORKTREE_REF_NAMESPACE),
    )
    # bwrap can only bind paths that exist.
    roots = tuple(path for path in candidates if os.path.isdir(path))
    if sys.platform == "darwin":
        # Deleting any ref (rebase drops CHERRY_PICK_HEAD) takes this lock even
        # when nothing is packed. Only the lock is writable, so packed-refs
        # itself, which may hold main, still cannot be replaced.
        roots += (os.path.join(common, "packed-refs.lock"),)
    return roots


def _seatbelt_profile(cwd: str, policy: SandboxPolicy, *, private_tmp: bool = False,
                      allow_subprocesses: bool = True, proxy_port: int | None = None) -> str:
    lines = [
        "(version 1)",
        "(deny default)",
        "(allow process-exec)",
        *(["(allow process-fork)"] if allow_subprocesses else []),
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
    if not private_tmp:
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
        if policy.network_domains is not None:
            if not isinstance(proxy_port, int) or not 1 <= proxy_port <= 65535:
                raise SandboxUnavailable("network domain policy requires a managed proxy")
            lines.append(f'(allow network-outbound (remote tcp "localhost:{proxy_port}"))')
        else:
            lines.append("(allow network*)")
    # Deny rules last — SBPL takes the last match, so these have to come
    # after the blanket read and write grants above.
    for rx in _regexes_for(policy.deny_read):
        lit = rx.replace('"', '\\"')
        lines.append(f'(deny file-read* (regex #"{lit}"))')
        # Otherwise a denied path is still probeable by unlinking it:
        # the error distinguishes "exists" from "no such file".
        lines.append(f'(deny file-write-unlink (regex #"{lit}"))')
    protect = not protected_writes.lifted(policy.writable_roots)
    if protect:
        for rx in _regexes_for(tuple(tree + "/**" for tree in protected_writes.home_trees())):
            lit = rx.replace('"', '\\"')
            lines.append(f'(deny file-write* (regex #"{lit}"))')
        # A session worktree lives under the state folder: its root stays
        # writable, and the rules below still apply inside it.
        for root in protected_writes.nested_roots(_writable_roots(cwd, policy)):
            lines.append(f"(allow file-write* (subpath {_sbpl_str(root)}))")
    for rx in _regexes_for(policy.deny_write):
        lit = rx.replace('"', '\\"')
        lines.append(f'(deny file-write* (regex #"{lit}"))')
    if protect:
        globs = (*protected_writes.name_globs(),
                 *(path + "/**" for path in protected_writes.repository_git_paths(cwd)))
        for rx in _regexes_for(globs):
            lit = rx.replace('"', '\\"')
            lines.append(f'(deny file-write* (regex #"{lit}"))')
    # SBPL last-match-wins: re-open only allow_read paths that beat deny.
    for path in _concrete_paths(policy.allow_read):
        if not read_is_denied(path, policy):
            lines.append(f'(allow file-read* (subpath {_sbpl_str(path)}))')
    return "\n".join(lines) + "\n"


def _bwrap_args(command: str, cwd: str, policy: SandboxPolicy, *,
                read_only_roots: tuple[str, ...] = ()) -> list[str]:
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
    # Re-expose trusted code/interpreters hidden by the private /tmp mount.
    # These mounts precede writable roots and all deny rules, so they cannot
    # reopen credentials or make the test snapshot writable.
    for root in read_only_roots:
        real = os.path.realpath(root)
        args += ["--ro-bind", real, real]
    roots = _writable_roots(cwd, policy)
    for root in roots:
        args += ["--bind", root, root]
    # Protected folders go before the read denials: a later bind of a
    # folder would cover a denial mounted inside it.
    protect = not protected_writes.lifted(policy.writable_roots)
    trees, names = protected_writes.concrete_paths(cwd, roots) if protect else ([], [])
    for tree in trees:
        args += ["--ro-bind", tree, tree]
    for root in protected_writes.nested_roots(roots) if protect else ():
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
    for path in (*_concrete_paths(policy.deny_write), *names):
        if os.path.exists(path):
            args += ["--ro-bind", path, path]
    args += ["--", "/bin/bash", "-c", command]
    return args


def _wsl_bwrap_args(
    command: str,
    cwd: str,
    policy: SandboxPolicy,
) -> list[str]:
    """Apply the bubblewrap policy inside the default WSL2 distribution.

    WSL2 is a delegation boundary, not an AppContainer imitation: commands
    run as Linux commands and Windows paths are translated with that distro's
    own ``wslpath``.  No Windows ACL or file mode is changed.
    """

    def translated(path: str) -> str:
        try:
            return platform_environment.windows_path_to_wsl(path)
        except RuntimeError as exc:
            raise SandboxUnavailable(str(exc)) from exc

    try:
        prefix = platform_environment.windows_wsl_exec_prefix()
    except RuntimeError as exc:
        raise SandboxUnavailable(str(exc)) from exc

    cwd = os.path.realpath(cwd)
    wsl_cwd = translated(cwd)
    args = [
        *prefix,
        "bwrap",
        "--new-session",
        "--die-with-parent",
        "--unshare-ipc",
        "--unshare-uts",
        "--cap-drop", "ALL",
        "--ro-bind", "/", "/",
        "--proc", "/proc",
        "--dev", "/dev",
    ]
    if not policy.host_process_info:
        args.insert(len(prefix) + 3, "--unshare-pid")
    if not policy.network:
        args.append("--unshare-net")

    args += ["--tmpfs", "/tmp", "--dir", "/tmp/openprogram-home"]
    roots = _writable_roots(cwd, policy)
    for root in roots:
        wsl_root = translated(root)
        args += ["--bind", wsl_root, wsl_root]
    protect = not protected_writes.lifted(policy.writable_roots)
    trees, names = protected_writes.concrete_paths(cwd, roots) if protect else ([], [])
    for tree in trees:
        args += ["--ro-bind", translated(tree), translated(tree)]
    for root in protected_writes.nested_roots(roots) if protect else ():
        args += ["--bind", translated(root), translated(root)]

    for path in _concrete_paths(policy.deny_read):
        if not read_is_denied(path, policy) or not os.path.exists(path):
            continue
        wsl_path = translated(path)
        if os.path.isdir(path):
            args += ["--perms", "0000", "--tmpfs", wsl_path]
        else:
            args += ["--ro-bind", "/dev/null", wsl_path]
    for path in _concrete_paths(policy.allow_read):
        if read_is_denied(path, policy) or not os.path.exists(path):
            continue
        wsl_path = translated(path)
        args += ["--ro-bind", wsl_path, wsl_path]
    for path in (*_concrete_paths(policy.deny_write), *names):
        if not os.path.exists(path):
            continue
        wsl_path = translated(path)
        args += ["--ro-bind", wsl_path, wsl_path]

    args += [
        "--clearenv",
        "--setenv", "PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "--setenv", "HOME", "/tmp/openprogram-home",
        "--setenv", "SHELL", "/bin/bash",
        "--setenv", "TMPDIR", "/tmp",
        "--setenv", "TMP", "/tmp",
        "--setenv", "TEMP", "/tmp",
    ]
    safe_env = child_env(policy)
    fixed = {"PATH", "HOME", "SHELL", "TMPDIR", "TMP", "TEMP", "PWD", "OLDPWD"}
    for name, value in safe_env.items():
        if name in fixed:
            continue
        args += ["--setenv", name, value]
    args += ["--chdir", wsl_cwd, "--", "/bin/bash", "-c", command]
    return args

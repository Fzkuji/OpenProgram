"""Tests for openprogram.sandbox — system-level sandbox."""
from __future__ import annotations

import os
import asyncio
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

import pytest

from openprogram import sandbox
from openprogram.sandbox import (
    DEFAULT_DENY_READ,
    MODE_WORKSPACE_WRITE,
    SandboxPolicy,
    SandboxUnavailable,
    _bwrap_unavailable_reason,
    _bwrap_args,
    _glob_to_regex,
    _seatbelt_profile,
    child_env,
    is_available,
    policy_from_dict,
    policy_hash,
    policy_snapshot,
    policy_to_dict,
    resolve_policy,
    validate_read_path,
    validate_write_path,
    wrap_command,
)


@pytest.fixture
def cfg(monkeypatch):
    """A config the sandbox module reads, without touching the real one."""
    state: dict = {}
    monkeypatch.setattr("openprogram.setup._read_config", lambda: state)
    monkeypatch.setattr(
        sandbox, "_process_policy_override", sandbox._NO_PROCESS_POLICY,
    )
    return state


def on(cfg: dict, **extra) -> None:
    cfg["sandbox"] = {"mode": MODE_WORKSPACE_WRITE, **extra}


# --- policy resolution -----------------------------------------------------

def test_workspace_write_by_default(cfg):
    assert resolve_policy() is not None


def test_explicit_off_disables_sandbox(cfg):
    cfg["sandbox"] = {"mode": "danger-full-access"}
    assert resolve_policy() is None


def test_mode_on_resolves_a_policy(cfg):
    on(cfg)
    pol = resolve_policy()
    assert pol is not None
    assert pol.deny_read == DEFAULT_DENY_READ
    assert pol.network is False


def test_required_policy_stays_on_when_config_mode_is_off(cfg):
    pol = resolve_policy(required=True)
    assert pol is not None
    assert any(p.endswith(os.path.join("applications", "**"))
               for p in pol.deny_write)


def test_policy_json_roundtrip_keeps_hash_and_hard_floor(cfg):
    original = SandboxPolicy(
        writable_roots=("/workspace/extra",),
        deny_read=("/secret",),
        deny_write=(),
        network=True,
        pass_env=("CARGO_HOME",),
    )
    encoded = policy_to_dict(original)
    restored = policy_from_dict(encoded)
    assert policy_hash(restored) == policy_hash(policy_from_dict(encoded))
    assert restored.writable_roots == original.writable_roots
    assert restored.network is True
    assert any(p.endswith(os.path.join("applications", "**"))
               for p in restored.deny_write)


def test_unknown_mode_reads_as_off(cfg):
    cfg["sandbox"] = {"mode": "banana"}
    assert resolve_policy() is None


def test_applications_directory_is_always_denied_write(cfg):
    """A user emptying deny_write must not be able to unblock the one
    directory whose contents get imported into the agent process."""
    on(cfg, deny_write=[])
    pol = resolve_policy()
    assert any(
        p.endswith(os.path.join("applications", "**")) for p in pol.deny_write
    )


def test_policy_resolves_in_a_fresh_thread(cfg):
    """The regression that made the web toggle a no-op: a bare thread
    starts with an empty Context, so anything carried on a ContextVar is
    lost. Config is not."""
    on(cfg)
    seen: list[bool] = []
    t = threading.Thread(target=lambda: seen.append(resolve_policy() is not None))
    t.start()
    t.join()
    assert seen == [True]


def test_installed_process_snapshot_survives_config_change_and_thread(
    cfg, monkeypatch,
):
    on(cfg, network=True, writable_roots=["/owner-extra"])
    snapshot = policy_snapshot()
    cfg["sandbox"]["mode"] = "danger-full-access"
    monkeypatch.setattr(
        sandbox, "_process_policy_override", sandbox._NO_PROCESS_POLICY,
    )
    sandbox.install_policy_snapshot(snapshot)

    seen = []
    thread = threading.Thread(target=lambda: seen.append(resolve_policy()))
    thread.start()
    thread.join()

    assert seen[0] is not None
    assert seen[0].network is True
    assert seen[0].writable_roots == ("/owner-extra",)


def test_write_path_uses_process_policy_roots(tmp_path, cfg, monkeypatch):
    work = tmp_path / "work"
    extra = tmp_path / "extra"
    outside = tmp_path / "outside"
    work.mkdir(); extra.mkdir(); outside.mkdir()
    monkeypatch.setattr(
        sandbox, "_process_policy_override", sandbox._NO_PROCESS_POLICY,
    )
    sandbox.install_policy_snapshot({
        "enabled": True,
        "policy": policy_to_dict(SandboxPolicy(
            writable_roots=(str(extra),),
            deny_read=(),
            deny_write=(str(extra / "blocked") + "/**",),
        )),
    })

    assert validate_write_path(work / "ok.txt", cwd=str(work)) is None
    assert validate_write_path(extra / "ok.txt", cwd=str(work)) is None
    assert validate_write_path(outside / "no.txt", cwd=str(work))
    assert validate_write_path(extra / "blocked" / "no.txt", cwd=str(work))


def test_write_tool_enforces_process_policy(tmp_path, cfg, monkeypatch):
    from openprogram.programs.tools.files.write import write

    work = tmp_path / "work"
    outside = tmp_path / "outside"
    work.mkdir(); outside.mkdir()
    monkeypatch.setattr(
        sandbox, "_process_policy_override", sandbox._NO_PROCESS_POLICY,
    )
    sandbox.install_policy_snapshot({
        "enabled": True,
        "policy": policy_to_dict(SandboxPolicy(deny_read=(), deny_write=())),
    })

    tool_result = asyncio.run(write.execute(
        "c", {"file_path": str(outside / "blocked.txt"), "content": "secret"},
        None, None,
    ))
    result = "".join(block.text for block in tool_result.content)

    assert result.startswith("Error: sandbox policy:")
    assert not (outside / "blocked.txt").exists()


# --- in-process read enforcement -------------------------------------------

def _install_deny_read(monkeypatch, *patterns: str) -> None:
    monkeypatch.setattr(
        sandbox, "_process_policy_override", sandbox._NO_PROCESS_POLICY,
    )
    sandbox.install_policy_snapshot({
        "enabled": True,
        "policy": policy_to_dict(SandboxPolicy(
            deny_read=patterns, deny_write=(),
        )),
    })


def _tool_text(tool, args) -> str:
    result = asyncio.run(tool.execute("c", args, None, None))
    return "".join(block.text for block in result.content)


@pytest.fixture
def secrets(tmp_path):
    """A workspace holding one denied file and one allowed file."""
    work = tmp_path / "work"
    vault = tmp_path / "vault"
    work.mkdir(); vault.mkdir()
    (vault / "id_rsa").write_text("PRIVATE KEY", encoding="utf-8")
    (work / "ok.txt").write_text("PRIVATE KEY lookalike", encoding="utf-8")
    return work, vault


def test_read_path_allows_everything_without_policy(cfg, secrets):
    _work, vault = secrets
    cfg["sandbox"] = {"mode": "danger-full-access"}
    assert validate_read_path(vault / "id_rsa") is None


def test_read_path_denies_configured_glob(cfg, monkeypatch, secrets):
    work, vault = secrets
    _install_deny_read(monkeypatch, str(vault) + "/**")
    assert validate_read_path(vault / "id_rsa")
    assert validate_read_path(work / "ok.txt") is None


def test_read_tool_refuses_denied_path(cfg, monkeypatch, secrets):
    from openprogram.programs.tools.files.read import read

    work, vault = secrets
    _install_deny_read(monkeypatch, str(vault) + "/**")

    denied = _tool_text(read, {"file_path": str(vault / "id_rsa")})
    assert denied.startswith("Error: sandbox policy:")
    assert "PRIVATE KEY" not in denied

    allowed = _tool_text(read, {"file_path": str(work / "ok.txt")})
    assert "PRIVATE KEY lookalike" in allowed


def test_list_tool_hides_denied_entries(cfg, monkeypatch, secrets):
    from openprogram.programs.tools.files.list import list_dir

    _work, vault = secrets
    _install_deny_read(monkeypatch, str(vault / "id_rsa"))

    out = _tool_text(list_dir, {"path": str(vault)})
    assert "id_rsa" not in out


def test_list_tool_refuses_denied_directory(cfg, monkeypatch, secrets):
    from openprogram.programs.tools.files.list import list_dir

    _work, vault = secrets
    _install_deny_read(monkeypatch, str(vault) + "/**")

    out = _tool_text(list_dir, {"path": str(vault)})
    assert out.startswith("Error: sandbox policy:")


def test_glob_tool_drops_denied_matches(cfg, monkeypatch, tmp_path):
    from openprogram.programs.tools.files.glob import glob_tool

    root = tmp_path / "root"
    (root / "keys").mkdir(parents=True)
    (root / "keys" / "id_rsa").write_text("k", encoding="utf-8")
    (root / "app.py").write_text("x", encoding="utf-8")
    _install_deny_read(monkeypatch, str(root / "keys") + "/**")

    out = _tool_text(glob_tool, {"pattern": "**/*", "path": str(root)})
    assert "id_rsa" not in out
    assert "app.py" in out


def test_grep_tool_drops_denied_files(cfg, monkeypatch, tmp_path):
    from openprogram.programs.tools.files.grep import grep

    root = tmp_path / "root"
    (root / "keys").mkdir(parents=True)
    (root / "keys" / "id_rsa").write_text("NEEDLE", encoding="utf-8")
    (root / "app.py").write_text("NEEDLE", encoding="utf-8")
    _install_deny_read(monkeypatch, str(root / "keys") + "/**")

    for mode in ("files_with_matches", "content", "count"):
        out = _tool_text(grep, {
            "pattern": "NEEDLE", "path": str(root), "output_mode": mode,
        })
        assert "id_rsa" not in out, mode
        assert "app.py" in out, mode


def test_sandbox_module_does_not_import_function_registry():
    """A broken tool import must not be able to break policy construction."""
    import ast
    source = Path(__file__).resolve().parents[3] / "openprogram/sandbox/__init__.py"
    with open(source, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    imported = {
        node.module for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name for node in ast.walk(tree)
        if isinstance(node, ast.Import) for alias in node.names
    }
    assert not any(name.startswith("openprogram.programs") for name in imported)


# --- glob translation ------------------------------------------------------

@pytest.mark.parametrize("pattern,expected", [
    ("/a/b", r"^/a/b$"),
    ("/a/b/**", r"^/a/b(/.*)?$"),
    ("**/.env", r"^(.*/)?\.env$"),
    ("/a/*.key", r"^/a/[^/]*\.key$"),
    ("/a/?.key", r"^/a/[^/]\.key$"),
    (".netrc", r"^(.*/)?\.netrc$"),
])
def test_glob_to_regex(pattern, expected):
    assert _glob_to_regex(pattern) == expected


def test_glob_regex_avoids_non_capturing_groups():
    """Seatbelt's regex engine silently fails to match `(?:...)`, which
    turns a deny rule into a no-op instead of an error."""
    for pattern in DEFAULT_DENY_READ + ("**/.env", "a/**/b"):
        assert "(?:" not in (_glob_to_regex(pattern) or "")


def test_glob_to_regex_expands_home():
    import re
    rx = _glob_to_regex("~/.ssh/**")
    assert rx == "^" + re.escape(os.path.expanduser("~/.ssh")) + "(/.*)?$"


# --- macOS profile ---------------------------------------------------------

def test_seatbelt_profile_allows_the_working_directory():
    p = _seatbelt_profile("/my/project", SandboxPolicy(deny_read=(), deny_write=()))
    assert '(allow file-write* (subpath "/my/project"))' in p
    assert "(version 1)" in p
    assert "(deny default)" in p


def test_seatbelt_profile_does_not_restrict_exec():
    """Children inherit the profile, so an exec allowlist only breaks the
    toolchain — git, python3 and make all live behind /usr/bin shims."""
    p = _seatbelt_profile("/w", SandboxPolicy())
    assert "(allow process-exec)" in p
    assert '(allow process-exec (subpath "/bin")' not in p


def test_seatbelt_profile_grants_the_character_devices():
    p = _seatbelt_profile("/w", SandboxPolicy())
    for dev in ("/dev/null", "/dev/zero", "/dev/urandom"):
        assert f'(literal "{dev}") (vnode-type CHARACTER-DEVICE)' in p


def test_seatbelt_profile_limits_signals_to_the_sandbox():
    p = _seatbelt_profile("/w", SandboxPolicy())
    assert "(allow signal (target same-sandbox))" in p
    assert "(allow process-info* (target same-sandbox))" in p


def test_seatbelt_profile_limits_sysctl_and_mach_services():
    p = _seatbelt_profile("/w", SandboxPolicy())
    assert '(sysctl-name-prefix "hw.")' in p
    assert '(sysctl-name "kern.ostype")' in p
    assert "(allow sysctl-read)" not in p
    assert "mach-lookup" not in p


def test_seatbelt_profile_only_allows_the_current_tmpdir(monkeypatch):
    tmpdir = "/private/var/folders/example/T"
    monkeypatch.setenv("TMPDIR", tmpdir)
    p = _seatbelt_profile("/w", SandboxPolicy())
    assert f'(allow file-write* (subpath "{tmpdir}"))' in p
    assert '(allow file-write* (subpath "/private/var/folders"))' not in p


def test_seatbelt_deny_read_emits_both_rules():
    p = _seatbelt_profile("/w", SandboxPolicy(deny_read=("/secret/**",),
                                              deny_write=()))
    assert '(deny file-read* (regex #"^/secret(/.*)?$"))' in p
    # Without the unlink rule a denied path is still probeable by trying
    # to delete it.
    assert '(deny file-write-unlink (regex #"^/secret(/.*)?$"))' in p


def test_seatbelt_denies_come_after_the_allows():
    """SBPL takes the last matching rule, so a deny placed above the
    blanket read grant does nothing."""
    p = _seatbelt_profile("/w", SandboxPolicy(deny_read=("/secret/**",)))
    assert p.index('(allow file-read* (subpath "/"))') < p.index("(deny file-read*")


def test_seatbelt_network_off_by_default():
    assert "(allow network" not in _seatbelt_profile("/w", SandboxPolicy())
    assert "(allow network*)" in _seatbelt_profile("/w", SandboxPolicy(network=True))


def test_seatbelt_escapes_the_working_directory():
    """A path that closes the string and opens another rule used to widen
    the write scope; the payload has to end up inert inside one literal."""
    evil = '/tmp/proj") (subpath "/Users/Shared'
    p = _seatbelt_profile(evil, SandboxPolicy(deny_read=(), deny_write=()))
    assert '(subpath "/Users/Shared")' not in p
    assert p.count("(allow file-write* (subpath ") == 4  # cwd + the 3 temp roots


def test_seatbelt_extra_writable_roots():
    p = _seatbelt_profile("/w", SandboxPolicy(writable_roots=("/extra",),
                                              deny_read=(), deny_write=()))
    assert '(allow file-write* (subpath "/extra"))' in p


# --- Linux arguments -------------------------------------------------------

def test_bwrap_mounts_tmpfs_before_binding_the_working_directory():
    """The other order lets `--tmpfs /tmp` cover a working directory under
    /tmp, and the workspace vanishes inside the sandbox."""
    args = _bwrap_args("echo hi", "/tmp/myproj",
                       SandboxPolicy(deny_read=(), deny_write=()))
    tmpfs = args.index("--tmpfs")
    bind = args.index("--bind")
    assert tmpfs < bind
    assert args[bind + 1] == args[bind + 2] == "/tmp/myproj"


def test_bwrap_isolates_the_pid_namespace():
    args = _bwrap_args("x", "/w", SandboxPolicy())
    for flag in ("--unshare-pid", "--new-session", "--die-with-parent",
                 "--cap-drop", "--unshare-net"):
        assert flag in args


def test_bwrap_network_flag_follows_the_policy():
    assert "--unshare-net" not in _bwrap_args("x", "/w",
                                              SandboxPolicy(network=True))


def test_bwrap_skips_deny_paths_that_do_not_exist(tmp_path):
    """The root is bound read-only, so bwrap cannot create a mount point
    for a missing path — it fails the whole invocation instead."""
    missing = str(tmp_path / "nope")
    args = _bwrap_args("x", "/w", SandboxPolicy(deny_read=(missing,),
                                                deny_write=()))
    assert missing not in args


def test_bwrap_masks_an_existing_deny_read_directory(tmp_path):
    secret = tmp_path / "secret"
    secret.mkdir()
    args = _bwrap_args("x", "/w", SandboxPolicy(deny_read=(str(secret) + "/**",),
                                                deny_write=()))
    i = args.index(str(secret))
    assert args[i - 1] == "--tmpfs"
    assert args[i - 3:i - 1] == ["--perms", "0000"]


def test_bwrap_masks_an_existing_deny_read_file(tmp_path):
    secret = tmp_path / "creds.json"
    secret.write_text("token")
    args = _bwrap_args("x", "/w", SandboxPolicy(deny_read=(str(secret),),
                                                deny_write=()))
    i = args.index(str(secret))
    assert args[i - 2:i] == ["--ro-bind", "/dev/null"]


def test_bwrap_ends_with_bash():
    args = _bwrap_args("echo hi", "/w", SandboxPolicy())
    assert args[-3:] == ["/bin/bash", "-c", "echo hi"]


def test_bwrap_availability_requires_working_namespaces(monkeypatch):
    class FailedProbe:
        returncode = 1
        stdout = ""
        stderr = "bwrap: No permissions to create new namespace"

    monkeypatch.setattr(sandbox.subprocess, "run", lambda *a, **kw: FailedProbe())
    reason = _bwrap_unavailable_reason("/test/bwrap-no-userns")
    assert reason is not None
    assert "cannot create the required namespaces" in reason


# --- child environment -----------------------------------------------------

def test_child_env_drops_credentials():
    env = child_env(SandboxPolicy(), {
        "PATH": "/bin", "HOME": "/h", "LC_ALL": "C",
        "OPENAI_API_KEY": "sk-secret", "GITHUB_TOKEN": "t",
        "SOME_NEW_PROVIDER_CREDENTIAL": "c",
    })
    assert env == {"PATH": "/bin", "HOME": "/h", "LC_ALL": "C"}


def test_child_env_passes_named_extras():
    env = child_env(SandboxPolicy(pass_env=("CARGO_HOME",)),
                    {"CARGO_HOME": "/c", "NOPE": "x"})
    assert env == {"CARGO_HOME": "/c"}


def test_child_env_refuses_a_credential_named_extra():
    """The escape hatch must not be able to hand a key to every command."""
    env = child_env(SandboxPolicy(pass_env=("MY_API_KEY", "AWS_SECRET")),
                    {"MY_API_KEY": "k", "AWS_SECRET": "s"})
    assert env == {}


# --- wrap_command ----------------------------------------------------------

def test_wrap_command_returns_a_list():
    args, shell = wrap_command("ls", "/tmp/test")
    assert isinstance(args, list)
    assert shell is False
    assert args[-3:] == ["/bin/bash", "-c", "ls"]


def test_is_available_returns_bool():
    assert isinstance(is_available(), bool)


# --- the backend seam ------------------------------------------------------

def test_invocation_plain_when_off(cfg):
    from openprogram.backend.local import _invocation
    cfg["sandbox"] = {"mode": "danger-full-access"}
    args, shell, env, sandboxed = _invocation("echo hi", cwd="/tmp")
    assert env is None
    assert sandboxed is False
    if sys.platform != "win32":
        assert (args, shell) == ("echo hi", True)


def test_invocation_wraps_when_on(cfg):
    from openprogram.backend.local import _invocation
    on(cfg)
    if not is_available():
        pytest.skip("no sandbox backend on this machine")
    args, shell, env, sandboxed = _invocation("echo hi", cwd="/tmp")
    assert isinstance(args, list) and shell is False
    assert "OPENAI_API_KEY" not in env
    assert sandboxed is True


def test_invocation_refuses_when_unavailable(cfg, monkeypatch):
    from openprogram.backend.local import _invocation
    on(cfg)
    cfg["sandbox"]["unavailable_policy"] = "refuse"
    monkeypatch.setattr(sandbox, "unavailable_reason", lambda: "no tool here")
    with pytest.raises(SandboxUnavailable) as e:
        _invocation("echo hi", cwd="/tmp")
    assert "no tool here" in str(e.value)
    assert "sandbox.mode=danger-full-access" in str(e.value)


def test_invocation_warns_and_runs_when_configured_to(cfg, monkeypatch):
    from openprogram.backend.local import _invocation
    on(cfg)
    cfg["sandbox"]["unavailable_policy"] = "warn"
    monkeypatch.setattr(sandbox, "unavailable_reason", lambda: "no tool here")
    args, shell, env, sandboxed = _invocation("echo hi", cwd="/tmp")
    assert env is None
    assert sandboxed is False


def test_run_reports_the_refusal_instead_of_raising(cfg, monkeypatch):
    from openprogram.backend.local import LocalBackend
    on(cfg)
    monkeypatch.setattr(sandbox, "unavailable_reason", lambda: "no tool here")
    r = LocalBackend().run("echo hi", timeout=5, cwd="/tmp")
    assert r.exit_code == 1
    assert "no tool here" in r.stderr
    assert r.sandbox_error == "unavailable"


@pytest.mark.parametrize(("stderr", "sandbox_error"), [
    ("bash: /outside/file: Operation not permitted", "denied"),
    ("ordinary command failure", None),
])
def test_run_structures_only_likely_sandbox_denials(
    cfg, monkeypatch, stderr, sandbox_error,
):
    from openprogram.backend.local import LocalBackend

    on(cfg)
    monkeypatch.setattr(sandbox, "unavailable_reason", lambda: None)
    completed = subprocess.CompletedProcess([], 1, stdout="", stderr=stderr)
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_kw: completed)
    result = LocalBackend().run("false", timeout=5, cwd="/tmp")
    assert result.sandbox_error == sandbox_error


def test_escalated_policy_preserves_only_the_hard_floor(cfg):
    on(cfg, deny_read=["/secret"], network=False)
    with sandbox.escalated_policy():
        policy = resolve_policy()
    assert policy is not None
    assert policy.network is True
    assert policy.deny_read == ()
    assert "/" in policy.writable_roots
    assert any(p.endswith(os.path.join("applications", "**"))
               for p in policy.deny_write)


# --- config surface --------------------------------------------------------

def test_sandbox_settings_are_registered():
    from openprogram.config_schema import SETTINGS, _BY_KEY
    keys = {s.key for s in SETTINGS}
    assert {"sandbox.mode", "sandbox.deny_read", "sandbox.allow_read",
            "sandbox.deny_write",
            "sandbox.writable_roots", "sandbox.network",
            "sandbox.unavailable_policy", "sandbox.pass_env"} <= keys
    assert _BY_KEY["sandbox.mode"].default == MODE_WORKSPACE_WRITE


def test_deny_read_ships_loaded():
    """Both harnesses that inspired this ship the engine with an empty
    list. The memory writer is a network-free egress path, so ours does
    not."""
    from openprogram.config_schema import _BY_KEY
    assert "~/.ssh/**" in _BY_KEY["sandbox.deny_read"].default
    assert "~/.openprogram/auth/**" in _BY_KEY["sandbox.deny_read"].default


def test_mode_setting_rejects_an_unknown_value(monkeypatch):
    from openprogram import config_schema
    monkeypatch.setattr(config_schema._setup, "update_config",
                        lambda mutator: None)
    assert "error" in config_schema.set_setting("sandbox.mode", "banana")


# --- end to end ------------------------------------------------------------

@pytest.mark.skipif(
    sys.platform != "darwin" or not os.path.exists("/usr/bin/sandbox-exec"),
    reason="macOS with sandbox-exec required",
)
class TestSeatbeltEndToEnd:
    def _run(self, cmd: str, cwd: str, policy: SandboxPolicy | None = None):
        args, _ = wrap_command(cmd, cwd, policy or SandboxPolicy())
        return subprocess.run(args, capture_output=True, text=True, timeout=20)

    def test_write_inside_cwd_allowed(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._run(f"echo ok > {td}/allowed.txt", td)
            assert r.returncode == 0
            assert os.path.exists(os.path.join(td, "allowed.txt"))

    def test_write_outside_cwd_denied(self):
        with tempfile.TemporaryDirectory() as td:
            # NOT the home directory: the policy allows writes under
            # /private/var/folders (and /tmp), which is exactly where a
            # temp HOME lands.
            target = "/Users/Shared/openprogram_sandbox_should_fail.txt"
            r = self._run(f"echo bad > {target}", td)
            assert r.returncode != 0
            assert not os.path.exists(target)

    def test_dev_null_redirect_works(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._run("ls /nonexistent 2>/dev/null; echo done", td)
            assert "done" in r.stdout
            assert "Operation not permitted" not in r.stderr

    def test_git_and_python_run(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._run("git --version && python3 -V", td)
            assert r.returncode == 0, r.stderr

    def test_deny_read_blocks_a_real_file(self):
        with tempfile.TemporaryDirectory() as td:
            secret = os.path.join(td, "secret.key")
            with open(secret, "w") as f:
                f.write("PRIVATE")
            r = self._run(f"cat {secret}", td,
                          SandboxPolicy(deny_read=(secret,), deny_write=()))
            assert "PRIVATE" not in r.stdout
            assert "Operation not permitted" in r.stderr

    def test_deny_read_glob_blocks_dotenv(self):
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, ".env"), "w") as f:
                f.write("SECRET=leak")
            r = self._run(f"cat {td}/.env", td,
                          SandboxPolicy(deny_read=("**/.env",), deny_write=()))
            assert "leak" not in r.stdout

    def test_injected_working_directory_does_not_widen_the_scope(self):
        with tempfile.TemporaryDirectory() as td:
            evil = os.path.join(td, 'proj") (subpath "/Users/Shared')
            os.makedirs(evil)
            target = "/Users/Shared/openprogram_sandbox_injected.txt"
            r = self._run(f"echo pwned > {target}", evil)
            assert r.returncode != 0
            assert not os.path.exists(target)


def test_process_info_same_sandbox_by_default_host_wide_when_escalated():
    """默认 profile 只允许查看同沙箱进程；escalated_policy 放开主机
    进程信息（ps/lsof 可用），signal 始终限制在同沙箱内。"""
    default_profile = _seatbelt_profile("/w", SandboxPolicy())
    assert "(allow process-info* (target same-sandbox))" in default_profile

    with sandbox.escalated_policy():
        escalated = sandbox.resolve_policy(required=True)
    assert escalated.host_process_info is True
    escalated_profile = _seatbelt_profile("/w", escalated)
    assert "(allow process-info*)\n" in escalated_profile + "\n"
    assert "(allow process-info* (target same-sandbox))" not in escalated_profile
    assert "(allow signal (target same-sandbox))" in escalated_profile

    # Linux 等价物：默认 --unshare-pid 隐藏主机进程，escalated 不再隐藏。
    assert "--unshare-pid" in _bwrap_args("true", "/w", SandboxPolicy())
    assert "--unshare-pid" not in _bwrap_args("true", "/w", escalated)

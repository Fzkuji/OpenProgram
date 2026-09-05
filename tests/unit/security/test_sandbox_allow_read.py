"""allow_read: narrower wins; hard floor cannot be opened."""
from __future__ import annotations

import os
from types import SimpleNamespace

from openprogram.protected_paths import applications_root
from openprogram.sandbox import (
    SANDBOX_DENIAL_GUIDANCE,
    SandboxPolicy,
    _bwrap_args,
    _seatbelt_profile,
    is_hard_floor_read,
    match_deny_read,
    named_denial_text,
    persist_allow_read,
    read_is_denied,
)


def test_narrower_allow_reopens_inside_wider_deny(tmp_path):
    denied = tmp_path / "vault"
    allowed = denied / "ok.txt"
    denied.mkdir()
    allowed.write_text("x")
    policy = SandboxPolicy(
        deny_read=(str(denied) + "/**",),
        allow_read=(str(allowed),),
        deny_write=(),
    )
    assert read_is_denied(str(denied / "secret"), policy)
    assert not read_is_denied(str(allowed), policy)


def test_equally_specific_deny_beats_allow(tmp_path):
    secret = tmp_path / "secret.env"
    secret.write_text("x")
    path = str(secret)
    policy = SandboxPolicy(
        deny_read=(path,),
        allow_read=(path,),
        deny_write=(),
    )
    assert read_is_denied(path, policy)


def test_hard_floor_ignores_allow_read():
    from openprogram.paths import get_state_dir
    auth = str(get_state_dir() / "auth" / "default.json")
    policy = SandboxPolicy(
        deny_read=(str(get_state_dir() / "auth") + "/**",),
        allow_read=(auth,),
        deny_write=(),
    )
    assert is_hard_floor_read(auth)
    assert read_is_denied(os.path.realpath(auth), policy)
    err = persist_allow_read(auth)
    assert err and "hard floor" in err
    assert persist_allow_read(applications_root())


def test_seatbelt_allow_follows_deny(tmp_path):
    allowed = tmp_path / "vault" / "ok.txt"
    allowed.parent.mkdir()
    allowed.write_text("x")
    profile = _seatbelt_profile("/w", SandboxPolicy(
        deny_read=(str(allowed.parent) + "/**",),
        allow_read=(str(allowed),),
        deny_write=(),
    ))
    deny_at = profile.index("(deny file-read*")
    escaped = os.path.realpath(allowed).replace("\\", "\\\\")
    allow_at = profile.index(f'(allow file-read* (subpath "{escaped}")')
    assert deny_at < allow_at


def test_bwrap_keeps_deny_mount_when_allow_is_equally_specific(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    args = _bwrap_args("x", "/w", SandboxPolicy(
        deny_read=(str(vault) + "/**",),
        allow_read=(str(vault),),
        deny_write=(),
    ))
    assert str(vault) in args


def test_bwrap_rebinds_narrower_allow_file(tmp_path):
    vault = tmp_path / "vault"
    allowed = vault / "ok.txt"
    vault.mkdir()
    allowed.write_text("x")
    args = _bwrap_args("x", "/w", SandboxPolicy(
        deny_read=(str(vault) + "/**",),
        allow_read=(str(allowed),),
        deny_write=(),
    ))
    i = args.index(str(vault))
    assert args[i - 1] == "--tmpfs"
    binds = [
        (args[n + 1], args[n + 2])
        for n, item in enumerate(args) if item == "--ro-bind"
    ]
    assert (str(allowed), str(allowed)) in binds


def test_named_denial_matches_command_path(tmp_path):
    secret = tmp_path / ".env"
    secret.write_text("k=v")
    policy = SandboxPolicy(deny_read=("**/.env",), deny_write=())
    hit = match_deny_read(f"cat {secret}\nOperation not permitted", policy)
    assert hit is not None
    path, rule = hit
    assert path == str(secret.resolve())
    assert rule == "**/.env"
    text = named_denial_text(path, rule)
    assert path in text and "**/.env" in text
    assert SANDBOX_DENIAL_GUIDANCE in text
    assert match_deny_read("false\nordinary failure", policy) is None


def test_validate_read_path_honors_narrower_allow(tmp_path, monkeypatch):
    from openprogram import sandbox
    from openprogram.sandbox import policy_to_dict, validate_read_path

    vault = tmp_path / "vault"
    allowed = vault / "ok.txt"
    vault.mkdir()
    allowed.write_text("x")
    monkeypatch.setattr(
        sandbox, "_process_policy_override", sandbox._NO_PROCESS_POLICY,
    )
    sandbox.install_policy_snapshot({
        "enabled": True,
        "policy": policy_to_dict(SandboxPolicy(
            deny_read=(str(vault) + "/**",),
            allow_read=(str(allowed),),
            deny_write=(),
        )),
    })
    assert validate_read_path(vault / "secret")
    assert validate_read_path(allowed) is None


def test_persist_allow_read_appends(monkeypatch):
    written = {}
    monkeypatch.setattr(
        "openprogram.setup._read_config",
        lambda: {"sandbox": {"allow_read": ["/already"]}},
    )
    monkeypatch.setattr(
        "openprogram.config_schema.set_setting",
        lambda key, value: written.update(key=key, value=value) or {"applied": "live"},
    )
    assert persist_allow_read("/tmp/ok.env") is None
    assert written["key"] == "sandbox.allow_read"
    assert "/already" in written["value"]
    assert os.path.realpath("/tmp/ok.env") in written["value"]


def test_system_prompt_forbids_relocating_secrets():
    from openprogram.context.components import build_system_prompt
    out = build_system_prompt({"id": "main", "name": "bot"})
    assert "never move or copy secrets" in out


def test_local_backend_attaches_matched_path_and_rule(monkeypatch):
    from openprogram import sandbox
    from openprogram.backend.local import LocalBackend

    secret = os.path.expanduser("~/.ssh/id_ed25519")
    monkeypatch.setattr(
        sandbox, "_process_policy_override", sandbox._NO_PROCESS_POLICY,
    )
    monkeypatch.setattr(
        "openprogram.setup._read_config",
        lambda: {"sandbox": {"mode": "workspace-write"}},
    )
    monkeypatch.setattr("openprogram.sandbox.unavailable_reason", lambda: None)
    if os.name == "nt":
        monkeypatch.setattr(
            sandbox._compat,
            "windows_wsl_exec_prefix",
            lambda: ["wsl.exe", "--distribution", "Ubuntu", "--exec"],
        )
        monkeypatch.setattr(
            sandbox._compat,
            "windows_path_to_wsl",
            lambda path: "/mnt/c/" + str(path).replace("\\", "/").replace(":", ""),
        )
    completed = SimpleNamespace(
        pid=123,
        returncode=1,
        stdout=None,
        stderr=None,
        communicate=lambda timeout=None: (
            "",
            f"cat: {secret}: Operation not permitted",
        ),
    )
    class Owner:
        def popen(self, *_a, **_kw):
            return completed

        def release(self):
            return None

        def terminate(self):
            return True

    monkeypatch.setattr("openprogram.backend.local.ProcessTreeOwner", Owner)
    result = LocalBackend().run(f"cat {secret}", timeout=5, cwd="/tmp")
    assert result.sandbox_error == "denied"
    assert result.sandbox_rule == "~/.ssh/**"
    assert result.sandbox_path

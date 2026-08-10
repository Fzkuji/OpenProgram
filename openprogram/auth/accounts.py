"""Account manager — isolated auth + subprocess environments.

An *account* is the unit of "these credentials belong together" and "any
subprocess running under this identity should not see the other
identity's tokens". Two motivating scenarios:

  1. One user with a personal and work OpenAI account. Running the
     personal agent must not see work tokens sitting in ``~/.codex/`` or
     ``$OPENAI_API_KEY``.
  2. Shared machines / CI runners. Each job gets its own account; nothing
     from the host shell bleeds in.

This is the credential account, not the workspace profile that
:mod:`openprogram.paths` manages (``--profile`` / ``~/.openprogram-<name>``,
which scopes config and sessions). The two are independent: one workspace
profile can hold many credential accounts.

The account root (``~/.openprogram/profiles/<name>/`` — the directory
keeps its old name so existing installs keep their credentials) owns:

  * ``auth/``       — where :class:`AuthStore` writes this account's pools
  * ``home/``       — the account's fake HOME. Subprocesses see
                      ``HOME=<root>/home``, ``XDG_CONFIG_HOME=<home>/.config``,
                      ``XDG_DATA_HOME=<home>/.local/share``, and similar
                      knobs for ``GNUPGHOME``, ``NPM_CONFIG_USERCONFIG``,
                      ``GH_CONFIG_DIR``. This is how hermes-agent achieves
                      process-level isolation; we follow the same pattern.
  * ``.env``        — key=value pairs that get merged into the subprocess
                      env. Account-specific ``OPENAI_API_KEY`` etc. live
                      here rather than in the outer shell.
  * ``metadata.json`` — display_name, created_at_ms, description

The manager itself is sync because directory creation and env assembly
are both cheap. Subprocess spawning uses :meth:`subprocess_env` to build
the env dict; we never ``os.environ``-mutate the host.

Account "default" exists implicitly — the first process to ask for it
creates it if needed. Other accounts are explicit: :meth:`create_account`
succeeds once, :meth:`delete_account` removes the whole tree (this is
executable because the files are ours; external CLI stores are handled
by :class:`RemovalStep` instead).
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .types import AuthConfigError, Account


DEFAULT_ACCOUNT_NAME = "default"


@dataclass
class AccountManager:
    """Creates, deletes, and hands out :class:`Account` objects.

    Keeps an in-memory index keyed by ``name`` so repeated lookups don't
    re-stat ``metadata.json``. The index is rebuilt lazily on each
    ``list_accounts()`` call — we don't watch for external changes because
    account CRUD happens exclusively through this class.
    """

    root: Path
    _cache: dict[str, Account] = None  # type: ignore[assignment]
    _lock: threading.RLock = None       # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser()
        self._cache = {}
        self._lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)
        # Ensure default account exists — users always get one without ceremony.
        if not (self.root / DEFAULT_ACCOUNT_NAME / "metadata.json").exists():
            self._materialize(
                name=DEFAULT_ACCOUNT_NAME,
                display_name="Default",
                description="Default account — used when no other is selected.",
            )

    # ---- CRUD ----------------------------------------------------------

    def create_account(
        self,
        name: str,
        *,
        display_name: str = "",
        description: str = "",
    ) -> Account:
        _validate_name(name)
        with self._lock:
            account_root = self.root / name
            if account_root.exists():
                raise AuthConfigError(f"account {name!r} already exists")
            return self._materialize(name, display_name, description)

    def get_account(self, name: str) -> Account:
        _validate_name(name)
        with self._lock:
            cached = self._cache.get(name)
            if cached is not None:
                return cached
            account_root = self.root / name
            meta_path = account_root / "metadata.json"
            if not meta_path.exists():
                if name == DEFAULT_ACCOUNT_NAME:
                    # Race: __post_init__ cleared cache before default
                    # metadata landed. Recreate rather than 404.
                    return self._materialize(DEFAULT_ACCOUNT_NAME, "Default", "")
                raise AuthConfigError(f"account {name!r} not found")
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            account = Account(
                name=name,
                root=account_root,
                created_at_ms=meta.get("created_at_ms", 0),
                display_name=meta.get("display_name", ""),
                description=meta.get("description", ""),
            )
            self._cache[name] = account
            return account

    def list_accounts(self) -> list[Account]:
        with self._lock:
            out: list[Account] = []
            for child in sorted(self.root.iterdir()):
                if not child.is_dir() or not (child / "metadata.json").exists():
                    continue
                out.append(self.get_account(child.name))
            return out

    def delete_account(self, name: str) -> None:
        _validate_name(name)
        if name == DEFAULT_ACCOUNT_NAME:
            raise AuthConfigError(
                "cannot delete the default account — it's created on demand"
            )
        with self._lock:
            account_root = self.root / name
            if not account_root.exists():
                raise AuthConfigError(f"account {name!r} not found")
            shutil.rmtree(account_root)
            self._cache.pop(name, None)

    # ---- subprocess helpers -------------------------------------------

    def subprocess_env(
        self,
        account: Account,
        *,
        base_env: Optional[dict[str, str]] = None,
    ) -> dict[str, str]:
        """Build an env dict for a subprocess running under ``account``.

        Layering (later wins):
          1. ``base_env`` (defaults to :data:`os.environ`)
          2. HOME / XDG_* overrides pointing at ``account.home_dir``
          3. ``account.env_file`` (``.env`` key=value merge)

        The returned dict is a fresh copy — safe to mutate before passing
        to ``subprocess.run`` / ``asyncio.create_subprocess_exec``.
        """
        env: dict[str, str] = dict(base_env if base_env is not None else os.environ)

        home = str(account.home_dir)
        account.home_dir.mkdir(parents=True, exist_ok=True)

        # Core HOME knobs — covers git, ssh, gpg, npm, gh, AWS, gcloud.
        overrides = {
            "HOME": home,
            "USERPROFILE": home,                                   # Windows
            "XDG_CONFIG_HOME": str(account.home_dir / ".config"),
            "XDG_DATA_HOME": str(account.home_dir / ".local" / "share"),
            "XDG_CACHE_HOME": str(account.home_dir / ".cache"),
            "XDG_STATE_HOME": str(account.home_dir / ".local" / "state"),
            "GNUPGHOME": str(account.home_dir / ".gnupg"),
            "NPM_CONFIG_USERCONFIG": str(account.home_dir / ".npmrc"),
            "GH_CONFIG_DIR": str(account.home_dir / ".config" / "gh"),
            # Flag so child programs that care can notice they're sandboxed.
            "OPENPROGRAM_PROFILE": account.name,
        }
        env.update(overrides)

        # Merge .env last so users can override even HOME if they really want.
        if account.env_file.exists():
            env.update(_read_dotenv(account.env_file))

        return env

    # ---- dotenv writers -----------------------------------------------

    def set_env_var(self, account: Account, key: str, value: str) -> None:
        """Set or update one key in the account's ``.env`` file.

        File format is a minimal ``KEY=VALUE`` subset: no interpolation,
        no multiline, no comments preserved across updates. That matches
        what ``.env``-reading libraries actually do, and keeps the file
        a trivial round-trippable format — important because the user
        may hand-edit it between runs.
        """
        _validate_env_key(key)
        with self._lock:
            current = _read_dotenv(account.env_file) if account.env_file.exists() else {}
            current[key] = value
            _write_dotenv(account.env_file, current)

    def unset_env_var(self, account: Account, key: str) -> None:
        _validate_env_key(key)
        with self._lock:
            if not account.env_file.exists():
                return
            current = _read_dotenv(account.env_file)
            current.pop(key, None)
            _write_dotenv(account.env_file, current)

    # ---- internals -----------------------------------------------------

    def _materialize(self, name: str, display_name: str, description: str) -> Account:
        account_root = self.root / name
        account_root.mkdir(parents=True, exist_ok=True)
        (account_root / "auth").mkdir(exist_ok=True)
        (account_root / "home").mkdir(exist_ok=True)
        meta = {
            "name": name,
            "display_name": display_name or name,
            "description": description,
            "created_at_ms": int(time.time() * 1000),
            "schema_v": 1,
        }
        (account_root / "metadata.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )
        account = Account(
            name=name,
            root=account_root,
            created_at_ms=meta["created_at_ms"],
            display_name=meta["display_name"],
            description=description,
        )
        self._cache[name] = account
        return account


# ---------------------------------------------------------------------------
# Validation + dotenv I/O
# ---------------------------------------------------------------------------

def _validate_name(name: str) -> None:
    if not name:
        raise AuthConfigError("account name cannot be empty")
    if "/" in name or "\\" in name or name.startswith("."):
        # Block path traversal — an account name like "../other" would escape
        # the root and trample someone else's directory.
        raise AuthConfigError(f"invalid account name: {name!r}")
    if len(name) > 64:
        raise AuthConfigError("account name too long (max 64 chars)")


def _validate_env_key(key: str) -> None:
    if not key:
        raise AuthConfigError("env key cannot be empty")
    if "=" in key or "\n" in key or "\0" in key:
        raise AuthConfigError(f"invalid env key: {key!r}")


def _read_dotenv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip matching quotes so users can paste values containing spaces.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            quote = value[0]
            value = value[1:-1]
            if quote == '"':
                value = value.replace('\\"', '"').replace("\\\\", "\\")
        out[key] = value
    return out


def _write_dotenv(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for key, value in values.items():
        if any(c in value for c in " \t'\"\\"):
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{key}="{escaped}"')
        else:
            lines.append(f"{key}={value}")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Module-level default manager
# ---------------------------------------------------------------------------

_default_manager: Optional[AccountManager] = None
_default_manager_lock = threading.Lock()


def get_account_manager() -> AccountManager:
    """Return the process-wide default :class:`AccountManager`.

    Root defaults to ``~/.openprogram/profiles`` — the directory keeps
    its old name deliberately, so this rename doesn't strand the
    credentials of every existing install. Override via
    :envvar:`OPENPROGRAM_HOME` pointing at an alternative base directory —
    tests + CI use this rather than monkey-patching ``Path.home``.
    """
    global _default_manager
    if _default_manager is None:
        with _default_manager_lock:
            if _default_manager is None:
                base = os.environ.get("OPENPROGRAM_HOME")
                if base:
                    root = Path(base).expanduser() / "profiles"
                else:
                    root = Path.home() / ".openprogram" / "profiles"
                _default_manager = AccountManager(root=root)
    return _default_manager


def set_account_manager_for_testing(manager: Optional[AccountManager]) -> None:
    """Override the default manager (tests only)."""
    global _default_manager
    _default_manager = manager


__all__ = [
    "DEFAULT_ACCOUNT_NAME",
    "AccountManager",
    "get_account_manager",
    "set_account_manager_for_testing",
]

"""Profile manager — isolated auth + subprocess environments.

A *profile* is the unit of "these credentials belong together" and "any
subprocess running under this identity should not see the other
identity's tokens". Two motivating scenarios:

  1. One user with a personal and work OpenAI account. Running the
     personal agent must not see work tokens sitting in ``~/.codex/`` or
     ``$OPENAI_API_KEY``.
  2. Shared machines / CI runners. Each job gets its own profile; nothing
     from the host shell bleeds in.

The profile root (``~/.openprogram/profiles/<name>/``) owns:

  * ``auth/``       — where :class:`AuthStore` writes this profile's pools
  * ``home/``       — the profile's fake HOME. Subprocesses see
                      ``HOME=<root>/home``, ``XDG_CONFIG_HOME=<home>/.config``,
                      ``XDG_DATA_HOME=<home>/.local/share``, and similar
                      knobs for ``GNUPGHOME``, ``NPM_CONFIG_USERCONFIG``,
                      ``GH_CONFIG_DIR``. This is how hermes-agent achieves
                      process-level isolation; we follow the same pattern.
  * ``.env``        — key=value pairs that get merged into the subprocess
                      env. Profile-specific ``OPENAI_API_KEY`` etc. live
                      here rather than in the outer shell.
  * ``metadata.json`` — display_name, created_at_ms, description

The manager itself is sync because directory creation and env assembly
are both cheap. Subprocess spawning uses :meth:`subprocess_env` to build
the env dict; we never ``os.environ``-mutate the host.

Profile "default" exists implicitly — the first process to ask for it
creates it if needed. Other profiles are explicit: :meth:`create_profile`
succeeds once, :meth:`delete_profile` removes the whole tree (this is
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

from .types import AuthConfigError, Profile


DEFAULT_PROFILE_NAME = "default"


@dataclass
class ProfileManager:
    """Creates, deletes, and hands out :class:`Profile` objects.

    Keeps an in-memory index keyed by ``name`` so repeated lookups don't
    re-stat ``metadata.json``. The index is rebuilt lazily on each
    ``list_profiles()`` call — we don't watch for external changes because
    profile CRUD happens exclusively through this class.
    """

    root: Path
    _cache: dict[str, Profile] = None  # type: ignore[assignment]
    _lock: threading.RLock = None       # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser()
        self._cache = {}
        self._lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)
        # Ensure default profile exists — users always get one without ceremony.
        if not (self.root / DEFAULT_PROFILE_NAME / "metadata.json").exists():
            self._materialize(
                name=DEFAULT_PROFILE_NAME,
                display_name="Default",
                description="Default profile — used when no other is selected.",
            )

    # ---- CRUD ----------------------------------------------------------

    def create_profile(
        self,
        name: str,
        *,
        display_name: str = "",
        description: str = "",
    ) -> Profile:
        _validate_name(name)
        with self._lock:
            profile_root = self.root / name
            if profile_root.exists():
                raise AuthConfigError(f"profile {name!r} already exists")
            return self._materialize(name, display_name, description)

    def get_profile(self, name: str) -> Profile:
        _validate_name(name)
        with self._lock:
            cached = self._cache.get(name)
            if cached is not None:
                return cached
            profile_root = self.root / name
            meta_path = profile_root / "metadata.json"
            if not meta_path.exists():
                if name == DEFAULT_PROFILE_NAME:
                    # Race: __post_init__ cleared cache before default
                    # metadata landed. Recreate rather than 404.
                    return self._materialize(DEFAULT_PROFILE_NAME, "Default", "")
                raise AuthConfigError(f"profile {name!r} not found")
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            profile = Profile(
                name=name,
                root=profile_root,
                created_at_ms=meta.get("created_at_ms", 0),
                display_name=meta.get("display_name", ""),
                description=meta.get("description", ""),
            )
            self._cache[name] = profile
            return profile

    def list_profiles(self) -> list[Profile]:
        with self._lock:
            out: list[Profile] = []
            for child in sorted(self.root.iterdir()):
                if not child.is_dir() or not (child / "metadata.json").exists():
                    continue
                out.append(self.get_profile(child.name))
            return out

    def delete_profile(self, name: str) -> None:
        _validate_name(name)
        if name == DEFAULT_PROFILE_NAME:
            raise AuthConfigError(
                "cannot delete the default profile — it's created on demand"
            )
        with self._lock:
            profile_root = self.root / name
            if not profile_root.exists():
                raise AuthConfigError(f"profile {name!r} not found")
            shutil.rmtree(profile_root)
            self._cache.pop(name, None)

    # ---- subprocess helpers -------------------------------------------

    def subprocess_env(
        self,
        profile: Profile,
        *,
        base_env: Optional[dict[str, str]] = None,
    ) -> dict[str, str]:
        """Build an env dict for a subprocess running under ``profile``.

        Layering (later wins):
          1. ``base_env`` (defaults to :data:`os.environ`)
          2. HOME / XDG_* overrides pointing at ``profile.home_dir``
          3. ``profile.env_file`` (``.env`` key=value merge)

        The returned dict is a fresh copy — safe to mutate before passing
        to ``subprocess.run`` / ``asyncio.create_subprocess_exec``.
        """
        env: dict[str, str] = dict(base_env if base_env is not None else os.environ)

        home = str(profile.home_dir)
        profile.home_dir.mkdir(parents=True, exist_ok=True)

        # Core HOME knobs — covers git, ssh, gpg, npm, gh, AWS, gcloud.
        overrides = {
            "HOME": home,
            "USERPROFILE": home,                                   # Windows
            "XDG_CONFIG_HOME": str(profile.home_dir / ".config"),
            "XDG_DATA_HOME": str(profile.home_dir / ".local" / "share"),
            "XDG_CACHE_HOME": str(profile.home_dir / ".cache"),
            "XDG_STATE_HOME": str(profile.home_dir / ".local" / "state"),
            "GNUPGHOME": str(profile.home_dir / ".gnupg"),
            "NPM_CONFIG_USERCONFIG": str(profile.home_dir / ".npmrc"),
            "GH_CONFIG_DIR": str(profile.home_dir / ".config" / "gh"),
            # Flag so child programs that care can notice they're sandboxed.
            "OPENPROGRAM_PROFILE": profile.name,
        }
        env.update(overrides)

        # Merge .env last so users can override even HOME if they really want.
        if profile.env_file.exists():
            env.update(_read_dotenv(profile.env_file))

        return env

    # ---- dotenv writers -----------------------------------------------

    def set_env_var(self, profile: Profile, key: str, value: str) -> None:
        """Set or update one key in the profile's ``.env`` file.

        File format is a minimal ``KEY=VALUE`` subset: no interpolation,
        no multiline, no comments preserved across updates. That matches
        what ``.env``-reading libraries actually do, and keeps the file
        a trivial round-trippable format — important because the user
        may hand-edit it between runs.
        """
        _validate_env_key(key)
        with self._lock:
            current = _read_dotenv(profile.env_file) if profile.env_file.exists() else {}
            current[key] = value
            _write_dotenv(profile.env_file, current)

    def unset_env_var(self, profile: Profile, key: str) -> None:
        _validate_env_key(key)
        with self._lock:
            if not profile.env_file.exists():
                return
            current = _read_dotenv(profile.env_file)
            current.pop(key, None)
            _write_dotenv(profile.env_file, current)

    # ---- internals -----------------------------------------------------

    def _materialize(self, name: str, display_name: str, description: str) -> Profile:
        profile_root = self.root / name
        profile_root.mkdir(parents=True, exist_ok=True)
        (profile_root / "auth").mkdir(exist_ok=True)
        (profile_root / "home").mkdir(exist_ok=True)
        meta = {
            "name": name,
            "display_name": display_name or name,
            "description": description,
            "created_at_ms": int(time.time() * 1000),
            "schema_v": 1,
        }
        (profile_root / "metadata.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )
        profile = Profile(
            name=name,
            root=profile_root,
            created_at_ms=meta["created_at_ms"],
            display_name=meta["display_name"],
            description=description,
        )
        self._cache[name] = profile
        return profile


# ---------------------------------------------------------------------------
# Validation + dotenv I/O
# ---------------------------------------------------------------------------

def _validate_name(name: str) -> None:
    if not name:
        raise AuthConfigError("profile name cannot be empty")
    if "/" in name or "\\" in name or name.startswith("."):
        # Block path traversal — a profile name like "../other" would escape
        # the root and trample someone else's directory.
        raise AuthConfigError(f"invalid profile name: {name!r}")
    if len(name) > 64:
        raise AuthConfigError("profile name too long (max 64 chars)")


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

_default_manager: Optional[ProfileManager] = None
_default_manager_lock = threading.Lock()


def get_profile_manager() -> ProfileManager:
    """Return the process-wide default :class:`ProfileManager`.

    Root defaults to ``~/.openprogram/profiles``. Override via
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
                _default_manager = ProfileManager(root=root)
    return _default_manager


def set_profile_manager_for_testing(manager: Optional[ProfileManager]) -> None:
    """Override the default manager (tests only)."""
    global _default_manager
    _default_manager = manager


__all__ = [
    "DEFAULT_PROFILE_NAME",
    "ProfileManager",
    "get_profile_manager",
    "set_profile_manager_for_testing",
]

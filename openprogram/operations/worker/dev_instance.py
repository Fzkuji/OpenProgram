"""The development instance of a source checkout, beside the official App.

The stable instance is the official release App
(``/Applications/OpenProgram.app``, port 18100, ``~/.openprogram``): it is
installed from a GitHub Release and updated only by its own updater, so
nothing here builds or installs it. The development instance is a runtime
(port 18200, ``~/.openprogram-dev``) that runs whatever ``dev`` commit was
deployed to it last, opened in ``/Applications/OpenProgram Dev.app`` on
macOS or in a browser. It builds from a managed detached worktree of the
source repository, so it does not depend on the state of a developer's
checkout.
"""
from __future__ import annotations

import json
import os
import plistlib
import subprocess
import urllib.request
from pathlib import Path
from openprogram.host._platform.filesystem import replace_file

DEV_PROFILE = "dev"
DEV_PORT = 18200
# Browser debugging port of the development App (scripts/local-instances/install-dev-app.sh);
# the stable App uses 9223.
DEV_APP_DEBUG_PORT = 9224
STABLE_PORT = 18100
DEV_BRANCH = "dev"
APP_PATH = Path("/Applications/OpenProgram.app")
SOURCE_REPO_FILE = "source-repo"


class InstanceError(Exception):
    """A deploy was refused; the message says why."""


def stable_state_dir() -> Path:
    return Path.home() / ".openprogram"


def dev_state_dir() -> Path:
    return Path.home() / f".openprogram-{DEV_PROFILE}"


def dev_runtime_dir() -> Path:
    return dev_state_dir() / "runtime"


def source_repo() -> Path:
    """The source checkout the development instance builds from.

    ``OPENPROGRAM_SOURCE_REPO`` wins; otherwise the path the last deploy
    recorded in ``~/.openprogram-dev/source-repo``.
    """
    value = os.environ.get("OPENPROGRAM_SOURCE_REPO", "").strip()
    if not value:
        try:
            value = (dev_state_dir() / SOURCE_REPO_FILE).read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            value = ""
    if not value:
        raise InstanceError(
            "no source checkout is known; set OPENPROGRAM_SOURCE_REPO or "
            "run scripts/local-instances/refresh-dev-instance.sh from the checkout once")
    repo = Path(value).expanduser()
    if not (repo / ".git").exists():
        raise InstanceError(f"the recorded source checkout is not a Git repository: {repo}")
    return repo


def _git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args],
                            capture_output=True, text=True)
    if check and result.returncode != 0:
        raise InstanceError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def _ref(repo: Path, ref: str) -> str | None:
    value = _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
    return value or None


def _read_revision(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return value or None


def dev_revision() -> str | None:
    """The commit the development instance was last deployed from."""
    return _read_revision(dev_runtime_dir() / "source-revision")


def dev_python() -> Path:
    """The development runtime's interpreter, from its manifest."""
    runtime = dev_runtime_dir()
    try:
        manifest = json.loads((runtime / "runtime-manifest.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise InstanceError("the development instance is not deployed; run `openprogram dev deploy`") from None
    return runtime / manifest["python"]


def auth_url() -> str:
    """A sign-in link for the development instance's current worker.

    The worker mints a new owner token every time it starts, so a browser
    signed in before a deploy has to sign in again with a fresh link.
    """
    result = subprocess.run(
        [str(dev_python()), "-I", "-B", "-m", "openprogram", "web", "auth-url",
         "--base-url", f"http://127.0.0.1:{DEV_PORT}"],
        capture_output=True, text=True, env={**os.environ, "OPENPROGRAM_PROFILE": DEV_PROFILE})
    url = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    if result.returncode != 0 or not url.startswith("http"):
        raise InstanceError(result.stderr.strip() or "the development instance gave no sign-in link; is it running?")
    return url


def stable_version() -> str | None:
    """The release version of the installed official App."""
    try:
        with (APP_PATH / "Contents" / "Info.plist").open("rb") as stream:
            value = plistlib.load(stream).get("CFBundleShortVersionString")
    except (OSError, plistlib.InvalidFileException):
        return None
    return value if isinstance(value, str) and value else None


def healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as response:
            return response.status == 200
    except OSError:
        return False


def _pending(repo: Path, deployed: str | None) -> list[dict]:
    """Commits on ``dev`` the development instance does not serve yet."""
    span = f"{deployed}..{DEV_BRANCH}" if deployed and _ref(repo, deployed) else DEV_BRANCH
    out = _git(repo, "log", "--format=%H%x1f%s", "--max-count=200", span, check=False)
    return [dict(zip(("sha", "subject"), line.split("\x1f", 1)))
            for line in out.splitlines() if "\x1f" in line]


def status() -> dict:
    """Both instances, and the ``dev`` commits a deploy would bring."""
    repo = source_repo()
    dev = dev_revision()
    return {
        "source_repo": str(repo),
        "stable": {
            "app": str(APP_PATH),
            "version": stable_version(),
            "port": STABLE_PORT,
            "healthy": healthy(STABLE_PORT),
        },
        "dev": {
            "branch": DEV_BRANCH,
            "branch_revision": _ref(repo, DEV_BRANCH),
            "deployed_revision": dev,
            "port": DEV_PORT,
            "url": f"http://127.0.0.1:{DEV_PORT}",
            "healthy": healthy(DEV_PORT),
        },
        "undeployed": _pending(repo, dev.removesuffix("-dirty") if dev else None),
    }


# Credentials the development instance may share. An API key is static, and an
# imported login reads another tool's login by reference. OAuth logins are not
# copied: their refresh tokens rotate, so two instances refreshing one login
# would sign each other out.
SHAREABLE_KINDS = frozenset({"api_key", "import"})
MODEL_CONFIG_KEYS = ("providers", "last_used_model")


def copy_models() -> dict:
    """Give the development instance the stable instance's model setup.

    Copies provider settings and the shareable credentials the development
    instance does not hold yet; returns what was copied and which providers'
    sign-ins must be signed in again there. The stable instance's files are
    only read: its store is opened without the carry-over, which is the
    stable instance's own to run.
    """
    from openprogram.models.auth.store import AuthStore

    from openprogram.models.auth.company_keys import COMPANY_MARK, KEYS_DIR

    source = AuthStore(root=stable_state_dir(), carry_over=False)
    target = AuthStore(root=dev_state_dir())
    copied, skipped = [], []
    # API keys are the companies' (``company_keys.py``): copy each company's.
    keys_dir = source.base_dir() / KEYS_DIR
    for path in sorted(keys_dir.glob("*.json")) if keys_dir.is_dir() else []:
        held = {(k.get("value") or "").strip() for k in target.company_keys(path.stem)}
        fresh = [k for k in source.company_keys(path.stem) if (k.get("value") or "").strip() not in held]
        for key in fresh:
            target.add_company_key(path.stem, key["value"], label=key.get("label") or "",
                                   source=key.get("source") or "web", key_id=key["id"])
        if fresh:
            copied.append(path.stem)
    for provider in source.providers():
        stored = [c for c in source.stored(provider).credentials if not c.metadata.get(COMPANY_MARK)]
        shareable = [c for c in stored if c.kind in SHAREABLE_KINDS]
        if len(shareable) < len(stored):
            skipped.append(provider)
        held = target.stored(provider).credentials
        ids = {c.id for c in held}
        secrets = {c.payload.auth_value for c in held if c.payload.auth_value}
        added = 0
        for cred in shareable:
            if cred.id in ids or (cred.payload.auth_value and cred.payload.auth_value in secrets):
                continue
            target.add_credential(cred)
            added += 1
        if added:
            copied.append(provider)

    config_path = dev_state_dir() / "config.json"
    stable_config = json.loads((stable_state_dir() / "config.json").read_text(encoding="utf-8"))
    try:
        dev_config = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        dev_config = {}
    for key in MODEL_CONFIG_KEYS:
        if key in stable_config:
            dev_config[key] = stable_config[key]
    staged = config_path.with_suffix(".json.incoming")
    staged.write_text(json.dumps(dev_config, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(staged, 0o600)
    replace_file(staged, config_path)
    return {"copied": copied, "oauth_not_copied": skipped}


def _managed_worktree(repo: Path, path: Path, revision: str) -> Path:
    """A clean detached worktree at ``revision``; ignored build caches stay."""
    if not (path / ".git").exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        _git(repo, "worktree", "add", "--detach", str(path), revision)
        return path
    _git(path, "checkout", "--force", "--detach", revision)
    _git(path, "clean", "-fd")
    return path


def _run(script: Path, cwd: Path) -> None:
    result = subprocess.run(["bash", str(script)], cwd=str(cwd))
    if result.returncode != 0:
        raise InstanceError(f"{script.name} failed (exit {result.returncode})")


def deploy_dev(ref: str = DEV_BRANCH) -> str:
    """Build ``ref`` into the development runtime and restart its worker."""
    repo = source_repo()
    revision = _ref(repo, ref)
    if not revision:
        raise InstanceError(f"unknown ref: {ref}")
    tree = _managed_worktree(repo, dev_state_dir() / "source", revision)
    _run(tree / "scripts" / "local-instances" / "refresh-dev-instance.sh", tree)
    return revision


def as_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)

"""Lightweight lifecycle checks for the fixed local embedding model."""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import platform
import shutil
import subprocess
import sys
from pathlib import Path


MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
MODEL_FILES = (
    "1_Pooling/config.json",
    "config.json",
    "config_sentence_transformers.json",
    "modules.json",
    "model.safetensors",
    "sentence_bert_config.json",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.txt",
)

# The ``embedding`` extra in pyproject.toml. Product runtimes ship without
# it — torch alone is several hundred megabytes — so the App installs these
# on demand into the state directory, never into its signed bundle.
PACKAGES = ("sentence-transformers>=6.1,<7", "torch>=2.13,<3")
_MODULES = ("sentence_transformers", "torch")
INSTALL_TIMEOUT_S = 1800

_INTEL_MACOS_UNAVAILABLE = (
    "semantic memory embeddings are unavailable on Intel macOS because no "
    "patched PyPI torch wheel is available; use BM25 on Intel macOS or run "
    "embeddings on macOS arm64/Linux"
)


def platform_unavailable_reason() -> str | None:
    """Return the embedding platform limitation, or ``None`` if supported."""
    if sys.platform == "darwin" and platform.machine() == "x86_64":
        return _INTEL_MACOS_UNAVAILABLE
    return None


def _snapshot_is_complete(snapshot: str | Path) -> bool:
    root = Path(snapshot)
    return all(
        (root / relative).is_file() and (root / relative).stat().st_size > 0
        for relative in MODEL_FILES
    )


def default_model_is_cached() -> bool:
    """Whether every required file exists without loading model code."""
    if platform_unavailable_reason() is not None:
        return False
    try:
        from huggingface_hub import snapshot_download

        snapshot = snapshot_download(
            MODEL_ID,
            allow_patterns=MODEL_FILES,
            local_files_only=True,
        )
        return _snapshot_is_complete(snapshot)
    except Exception:
        return False


def packages_dir() -> Path:
    """Where the on-demand packages live, one directory per interpreter."""
    from openprogram.state.paths import get_state_dir

    from openprogram.host._platform.architecture import interpreter_architecture

    tag = (
        f"py{sys.version_info.major}{sys.version_info.minor}"
        f"-{sys.platform}-{interpreter_architecture()}"
    )
    return get_state_dir() / "extras" / "embedding" / tag


def activate_packages() -> None:
    """Put installed on-demand packages on ``sys.path``.

    Appended, not prepended: a dependency the runtime already ships, such
    as numpy, keeps resolving to the runtime's own copy.
    """
    path = packages_dir()
    if str(path) in sys.path or not path.is_dir():
        return
    sys.path.append(str(path))
    importlib.invalidate_caches()


def packages_available() -> bool:
    """Whether sentence-transformers and torch can be imported."""
    if platform_unavailable_reason() is not None:
        return False
    activate_packages()
    try:
        return all(importlib.util.find_spec(name) is not None for name in _MODULES)
    except (ImportError, ValueError):
        return False


def semantic_available() -> bool:
    """Whether semantic recall can run: packages and model files both present."""
    return packages_available() and default_model_is_cached()


def _runtime_constraints() -> list[str]:
    """Pin every distribution the runtime ships to the version it ships.

    A shared dependency then resolves to the same version in both places,
    so which copy ``import`` finds first cannot matter.
    """
    pins = set()
    for dist in importlib.metadata.distributions():
        name = dist.metadata["Name"] if dist.metadata else None
        if name and dist.version:
            pins.add(f"{name}=={dist.version}")
    return sorted(pins)


def install_packages() -> None:
    """Install sentence-transformers and torch beside the runtime.

    Into a staging directory first, moved into place only once pip has
    finished, so an interrupted install never leaves half a package where
    ``activate_packages`` would pick it up.
    """
    reason = platform_unavailable_reason()
    if reason is not None:
        raise RuntimeError(reason)
    if packages_available():
        return
    target = packages_dir()
    staging = target.with_name(f"{target.name}.installing")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    constraints = staging.parent / f"{target.name}.constraints.txt"
    constraints.write_text("\n".join(_runtime_constraints()) + "\n", encoding="utf-8")
    try:
        result = subprocess.run(
            [
                sys.executable, "-I", "-m", "pip", "install",
                "--disable-pip-version-check", "--no-input", "--quiet",
                "--target", str(staging), "--constraint", str(constraints),
                *PACKAGES,
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=INSTALL_TIMEOUT_S,
        )
        if result.returncode != 0:
            tail = (result.stderr or result.stdout).strip().splitlines()[-5:]
            raise RuntimeError(
                "Installing sentence-transformers failed: " + " ".join(tail)
            )
        shutil.rmtree(target, ignore_errors=True)
        staging.rename(target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        constraints.unlink(missing_ok=True)
    activate_packages()
    if not packages_available():
        raise RuntimeError("sentence-transformers was installed but cannot be imported")


def install_default_model() -> None:
    """Download and verify the fixed encoder snapshot."""
    reason = platform_unavailable_reason()
    if reason is not None:
        raise RuntimeError(reason)
    from huggingface_hub import snapshot_download

    snapshot = snapshot_download(MODEL_ID, allow_patterns=MODEL_FILES)
    if not _snapshot_is_complete(snapshot):
        raise OSError("Embedding model download is incomplete")

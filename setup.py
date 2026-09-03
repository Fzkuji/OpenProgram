"""Bind wheel runtime identity to the source used by the shared build backend."""
from pathlib import Path
import re
import subprocess

from setuptools import setup
from setuptools.command.build_py import build_py


def source_revision():
    root = Path(__file__).resolve().parent
    if not (root / ".git").exists():
        return ""  # An sdist must not inherit an unrelated parent checkout's SHA.
    try:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True, stderr=subprocess.DEVNULL, timeout=5).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True, stderr=subprocess.DEVNULL, timeout=5).strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        return ""
    return revision + ("-dirty" if dirty else "")


class BuildPy(build_py):
    def run(self):
        if self.editable_mode:
            return super().run()  # Editable installs retain checkout identity.
        revision = source_revision()
        super().run()
        if source_revision() != revision:
            raise RuntimeError("source identity changed during wheel build")
        marker = Path(self.build_lib) / "openprogram" / "_build_revision.txt"
        marker.write_text(revision + "\n", encoding="ascii")


setup(cmdclass={"build_py": BuildPy})

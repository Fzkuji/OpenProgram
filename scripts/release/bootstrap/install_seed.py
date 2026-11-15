"""Finish a first install from a seed: stdlib only, run with the base's Python.

A seed is the code a release carries: a base, one sealed version, the
repository bundle, the launcher and these bootstrap tools. The App's shell
unpacks the archives itself to show progress and then runs this; the
command-line installers unpack the base with ``tar`` and let this unpack the
rest (``--extract``).

``.seeding`` is present for the whole run and removed last, so an interrupted
install starts over cleanly. A non-empty ``repo/`` is never overwritten.
"""
from __future__ import annotations

import argparse
import errno
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile

PYVENV = "venv/pyvenv.cfg"
TOOLS = ("versions.py", "restore_version.py")
# Git writes paths in UTF-8 whatever the system code page is; a message that
# names a folder in another script must not stop the install while being read.
GIT_TEXT = {"encoding": "utf-8", "errors": "replace"}


class SeedError(RuntimeError):
    """The install stopped; the message says why."""


def _base_python_path(base: Path) -> Path:
    return base / ("python/python.exe" if os.name == "nt" else "python/bin/python3")


def _load(seed: Path) -> dict:
    try:
        manifest = json.loads((seed / "seed.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SeedError(f"the seed has no readable seed.json: {exc}") from exc
    for key in ("version", "base", "commit", "built_root"):
        if not isinstance(manifest.get(key), str) or not manifest[key]:
            raise SeedError(f"seed.json has no {key}")
    return manifest


def _member_is_safe(name: str) -> bool:
    return not name.startswith("/") and "\\" not in name and ".." not in name.split("/")


def link_stays(target: str, built_root: str) -> bool:
    """Whether an absolute link target is under the root the seed was built in.

    Compared with one kind of slash: Windows' tar writes ``C:/…`` for a root
    that ``seed.json`` records as ``C:\\…``.
    """
    link, built = target.replace("\\", "/"), built_root.replace("\\", "/").rstrip("/")
    if not link.startswith("/") and not re.match(r"[A-Za-z]:", link):
        return True  # Relative.
    if re.match(r"[A-Za-z]:", built):
        link, built = link.lower(), built.lower()
    return link.startswith(f"{built}/") and ".." not in link.split("/")


def _extract_tar(archive: Path, destination: Path, built_root: str) -> None:
    """Unpack a seed archive, keeping its links, into ``destination``."""
    with tarfile.open(archive) as tar:
        for member in tar:
            if not _member_is_safe(member.name):
                raise SeedError(f"invalid path in the seed: {member.name}")
            if member.issym() and not link_stays(member.linkname, built_root):
                raise SeedError(f"a link in the seed leaves it: {member.name}")
            tar.extract(member, destination, filter="fully_trusted")


def _when_unlocked(operation, *, attempts: int = 8, delay: float = 0.25) -> None:
    """Wait out another program that holds a new file, as scanners do on Windows."""
    for attempt in range(1, attempts + 1):
        try:
            operation()
            return
        except OSError as exc:
            if exc.errno not in (errno.EPERM, errno.EBUSY, errno.EACCES) or attempt == attempts:
                raise
            time.sleep(delay * attempt)


def _move_each(source: Path, target: Path) -> None:
    if not source.is_dir():
        return
    target.mkdir(parents=True, exist_ok=True)
    for entry in source.iterdir():
        destination = target / entry.name
        if destination.exists():
            _when_unlocked(lambda: shutil.rmtree(entry) if entry.is_dir() else entry.unlink())
        else:
            _when_unlocked(lambda: os.replace(entry, destination))


def extract(seed: Path, root: Path, manifest: dict) -> None:
    work = root / ".seed-work"
    shutil.rmtree(work, ignore_errors=True)
    if not (root / "bases" / manifest["base"]).exists():
        _extract_tar(seed / "base.tar.gz", work, manifest["built_root"])
    if not (root / "versions" / manifest["version"]).exists():
        _extract_tar(seed / "version.tar.gz", work, manifest["built_root"])
    _move_each(work / "bases", root / "bases")
    _move_each(work / "pristine-frontend", root / "pristine-frontend")
    _move_each(work / "versions", root / "versions")
    shutil.rmtree(work, ignore_errors=True)


def _site_links(version_dir: Path) -> list[Path]:
    binary = version_dir / "venv" / ("Scripts" if os.name == "nt" else "bin")
    return [entry for entry in sorted(binary.iterdir()) if entry.is_symlink()] if binary.is_dir() else []


def relocate(root: Path, version: str, built_root: str) -> None:
    """Rename the build machine's root to this one wherever a version names it.

    ``pristine.zip`` gets the corrected entries appended, so a later direct-edit
    capture restores files that work here. Entries added last win when read.
    """
    version_dir = root / "versions" / version
    new_root = str(root)
    changed: list[str] = []
    config = version_dir / PYVENV
    if config.is_file():
        text = config.read_text(encoding="utf-8")
        if built_root in text:
            config.write_text(text.replace(built_root, new_root), encoding="utf-8")
            changed.append(PYVENV)
    for link in _site_links(version_dir):
        target = os.readlink(link)
        if target.startswith(f"{built_root}/"):
            link.unlink()
            link.symlink_to(new_root + target[len(built_root):])
            changed.append(link.relative_to(version_dir).as_posix())
    if changed and built_root != new_root:
        with zipfile.ZipFile(version_dir / "pristine.zip", "a", allowZip64=True) as archive:
            for name in changed:
                _append(archive, version_dir / name, name)
    _rewrite_files(version_dir)


def _append(archive: zipfile.ZipFile, source: Path, name: str) -> None:
    info = source.lstat()
    entry = zipfile.ZipInfo(name, date_time=time.gmtime(max(info.st_mtime, 315532800))[:6])
    entry.create_system = 3
    entry.external_attr = (info.st_mode & 0xFFFF) << 16
    data = os.readlink(source).encode("utf-8") if source.is_symlink() else source.read_bytes()
    with _quiet_duplicates():
        archive.writestr(entry, data)


class _quiet_duplicates:
    """Appending a newer entry of the same name is the point here."""

    def __enter__(self):
        import warnings

        self._catcher = warnings.catch_warnings()
        self._catcher.__enter__()
        warnings.simplefilter("ignore", UserWarning)

    def __exit__(self, *details):
        self._catcher.__exit__(*details)


def _rewrite_files(version_dir: Path) -> None:
    """Unpacking changed sizes and times of what the seal records."""
    table = version_dir / "files.tsv"
    lines = []
    for line in table.read_text(encoding="utf-8").splitlines():
        relative = line.split("\t")[0]
        info = (version_dir / relative).lstat()
        lines.append(f"{relative}\t{info.st_size}\t{info.st_mtime_ns}\n")
    table.write_text("".join(lines), encoding="utf-8", newline="\n")


def install_identity(seed: Path, root: Path) -> None:
    identity = root / "identity"
    identity.mkdir(parents=True, exist_ok=True)
    helper = seed / "helper" / "OpenProgram.app"
    if helper.is_dir():
        target = identity / "OpenProgram.app"
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(helper, target, symlinks=True)
    for name in ("openprogram-launcher.exe", "openprogram-launcher"):
        launcher = seed / name
        if launcher.is_file():
            shutil.copy2(launcher, identity / name)
    for name in ("shell-compat.json", "python-minor"):
        source = seed / name
        if source.is_file():
            shutil.copy2(source, identity / name)


def write_python_minor(root: Path, manifest: dict) -> None:
    """The launcher embeds its base's Python line, and the version store checks it."""
    if not (root / "identity" / "python-minor").exists():
        match = re.fullmatch(r"py(\d+\.\d+)-[0-9a-f]+", manifest["base"])
        if match is not None:
            (root / "identity" / "python-minor").write_text(match[1], encoding="ascii")


def install_tools(seed: Path, root: Path) -> None:
    bootstrap = root / "bootstrap"
    bootstrap.mkdir(parents=True, exist_ok=True)
    for name in TOOLS:
        shutil.copy2(seed / "bootstrap" / name, bootstrap / name)
    binary = root / "bin"
    binary.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        write_batch_file(seed / "bootstrap" / "bin-openprogram.cmd", binary / "openprogram.cmd")
    else:
        command = binary / "openprogram"
        shutil.copy2(seed / "bootstrap" / "bin-openprogram.sh", command)
        command.chmod(0o755)


def write_batch_file(source: Path, target: Path) -> None:
    """Install a batch file with the line ends cmd.exe needs.

    The repository keeps it with bare line feeds, and cmd.exe can miss a
    ``goto`` label in such a file, depending on where the label falls.
    """
    target.write_bytes(b"".join(line + b"\r\n" for line in source.read_bytes().splitlines()))


def git_executable(git: Path, windows: bool) -> Path:
    """The Git command inside a base's ``git`` directory.

    The bundled Git for Windows keeps it in ``cmd``; its ``bin`` does not exist.
    """
    return git / "cmd" / "git.exe" if windows else git / "bin" / "git"


def _git_command(base: Path) -> tuple[str, dict[str, str]]:
    environment = dict(os.environ)
    git = base / "git"
    if not git.is_dir():
        found = shutil.which("git")
        if found is None:
            raise SeedError(
                "OpenProgram needs Git to finish setting up, and this computer doesn't have it. "
                "Install Git (on Debian or Ubuntu: sudo apt install git), then try again."
            )
        return found, environment
    if os.name == "nt":
        return str(git_executable(git, True)), environment
    environment["GIT_EXEC_PATH"] = str(git / "libexec" / "git-core")
    environment["GIT_TEMPLATE_DIR"] = str(git / "share" / "git-core" / "templates")
    return str(git_executable(git, False)), environment


def _git(command: tuple[str, dict[str, str]], repository: Path, *arguments: str) -> None:
    executable, environment = command
    result = subprocess.run(
        [executable, "-C", str(repository), *arguments],
        capture_output=True, env=environment, timeout=300, **GIT_TEXT,
    )
    if result.returncode != 0:
        raise SeedError(f"git {arguments[0]} failed: {result.stderr.strip()}")


def create_repository(seed: Path, root: Path, manifest: dict) -> None:
    repository = root / "repo"
    if repository.is_dir() and any(repository.iterdir()):
        return
    git = _git_command(root / "bases" / manifest["base"])
    staging = root / f".repo-{os.getpid()}"
    shutil.rmtree(staging, ignore_errors=True)
    result = subprocess.run(
        [git[0], "clone", "--quiet", "--bare", str(seed / "repo.bundle"), str(staging)],
        capture_output=True, env=git[1], timeout=600, **GIT_TEXT,
    )
    if result.returncode != 0:
        shutil.rmtree(staging, ignore_errors=True)
        raise SeedError(f"could not create the code folder: {result.stderr.strip()}")
    commit = manifest["commit"]
    _git(git, staging, "update-ref", "refs/heads/local", commit)
    _git(git, staging, "update-ref", "refs/heads/release", commit)
    _git(git, staging, "update-ref", f"refs/openprogram/version/{manifest['version']}", commit)
    _git(git, staging, "symbolic-ref", "HEAD", "refs/heads/local")
    _git(git, staging, "config", "core.fsync", "committed,loose-object")
    if repository.exists():
        repository.rmdir()
    os.replace(staging, repository)


def _pointer_text(manifest: dict, generation: int, writer: str) -> str:
    fields = [
        ("schema", "1"), ("generation", str(generation)), ("version", manifest["version"]),
        ("base", manifest["base"]), ("last_good", manifest["version"]), ("last_good_base", manifest["base"]),
        ("previous_good", ""), ("previous_good_base", ""), ("probation", ""), ("probation_deadline", ""),
        ("writer", writer),
    ]
    return "".join(f"{key}={value}\n" for key, value in fields)


def _read_generation(pointer: Path) -> int | None:
    try:
        for line in pointer.read_text(encoding="ascii").splitlines():
            if line.startswith("generation=") and line[11:].isdigit():
                return int(line[11:])
    except (OSError, UnicodeDecodeError):
        pass
    return None


def write_pointer(root: Path, manifest: dict, *, point_at_seed: bool) -> None:
    pointer = root / "current"
    generation = _read_generation(pointer)
    if generation is not None and not point_at_seed:
        return
    text = _pointer_text(manifest, (generation or 0) + 1, "installer")
    temporary = root / f"current.{os.getpid()}.tmp"
    with open(temporary, "wb") as stream:
        stream.write(text.encode("ascii"))
        stream.flush()
        os.fsync(stream.fileno())
    if pointer.exists():
        shutil.copy2(pointer, root / "current.prev")
    os.replace(temporary, pointer)
    if os.name != "nt":
        descriptor = os.open(root, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def install(seed: Path, root: Path, *, unpack: bool, point_at_seed: bool) -> dict:
    manifest = _load(seed)
    root.mkdir(parents=True, exist_ok=True)
    (root / ".seeding").write_text(f"{os.getpid()}\n", encoding="ascii")
    if unpack:
        extract(seed, root, manifest)
    if not _base_python_path(root / "bases" / manifest["base"]).exists():
        raise SeedError("the seed's base did not unpack")
    relocate(root, manifest["version"], manifest["built_root"])
    install_identity(seed, root)
    write_python_minor(root, manifest)
    install_tools(seed, root)
    create_repository(seed, root, manifest)
    write_pointer(root, manifest, point_at_seed=point_at_seed)
    (root / ".seeding").unlink(missing_ok=True)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--extract", action="store_true", help="Unpack the .tar.gz archives too.")
    parser.add_argument("--point-at-seed", action="store_true", help="Move an existing pointer to the seed's version.")
    arguments = parser.parse_args(argv)
    try:
        manifest = install(
            arguments.seed.resolve(), arguments.root.resolve(),
            unpack=arguments.extract, point_at_seed=arguments.point_at_seed,
        )
    except (SeedError, OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        print(f"openprogram: {exc}", file=sys.stderr)
        return 1
    print(manifest["version"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

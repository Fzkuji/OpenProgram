#!/usr/bin/env python3
"""Write or verify the complete OpenProgram product-runtime manifest."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _relative_file(root: Path, value: str, label: str) -> Path:
    path = (root / value).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise RuntimeError(f"{label} escapes the runtime: {value}") from exc
    if not path.is_file():
        raise RuntimeError(f"{label} is missing: {path}")
    return path


def _relative_dir(root: Path, value: str, label: str) -> Path:
    path = (root / value).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise RuntimeError(f"{label} escapes the runtime: {value}") from exc
    if not path.is_dir():
        raise RuntimeError(f"{label} is missing: {path}")
    return path


def _verify_openprogram_version(expected: object) -> None:
    if not isinstance(expected, str) or not expected:
        raise RuntimeError("runtime manifest OpenProgram version is invalid")
    actual = importlib.metadata.version("openprogram")
    if actual != expected:
        raise RuntimeError(
            f"OpenProgram version mismatch: expected {expected}, got {actual}"
        )


def _probe_pdf_tools() -> None:
    from pypdf import PdfWriter

    from openprogram.programs.tools.web.pdf import execute as pdf_extract
    from openprogram.programs.tools.files.read import _read_pdf

    with tempfile.TemporaryDirectory(prefix="openprogram-pdf-probe-") as temp_dir:
        pdf_path = Path(temp_dir) / "probe.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        with pdf_path.open("wb") as stream:
            writer.write(stream)

        pdf_result = pdf_extract(file_path=str(pdf_path))
        read_result = _read_pdf(str(pdf_path), offset=1, limit=1)
        if "unavailable" in pdf_result.lower():
            raise RuntimeError("built-in pdf tool is unavailable")
        if "unavailable" in read_result.lower():
            raise RuntimeError("built-in read PDF support is unavailable")


_FORBIDDEN_RUNTIME_DISTS = frozenset(
    {
        "easyocr",
        "opencv-contrib-python",
        "opencv-contrib-python-headless",
        "opencv-python",
        "opencv-python-headless",
        "torch",
        "torchvision",
        "sentence-transformers",
        "triton",
    }
)


def _reject_excluded_runtime_wheels() -> None:
    leftover = []
    for dist in importlib.metadata.distributions():
        name = dist.metadata["Name"] or ""
        key = name.lower().replace("_", "-").replace(".", "-")
        if key in _FORBIDDEN_RUNTIME_DISTS or key.startswith(("nvidia-", "cuda-")):
            leftover.append(name)
    if leftover:
        raise RuntimeError(
            "product runtime must not ship excluded distributions or wheels: "
            + ", ".join(sorted(leftover))
        )


def _probe_rich_terminal() -> None:
    from rich.console import Console

    output = io.StringIO()
    Console(file=output, force_terminal=False).print("OpenProgram terminal probe")
    if "OpenProgram terminal probe" not in output.getvalue():
        raise RuntimeError("built-in Rich terminal UI probe failed")


def _probe_ink_terminal(root: Path, tui: Path | None = None) -> tuple[str, str]:
    """Verify the self-contained Node + Ink frontend shipped in the runtime.

    A sealed version carries its own bundle, given as ``tui``.
    """
    node_relative = "bin/node.exe" if os.name == "nt" else "bin/node"
    node = _relative_file(root, node_relative, "bundled Node.js")
    entry_relative = "assets/tui/index.cjs"
    entry = tui if tui is not None else _relative_file(root, entry_relative, "bundled Ink TUI")
    if sys.platform.startswith("linux"):
        pty_smoke = _relative_file(
            root,
            "bin/smoke-ink-tui-pty.py",
            "Ink pseudo-terminal smoke test",
        )
        command = [
            sys.executable,
            "-I",
            "-B",
            str(pty_smoke),
            str(node),
            str(entry),
        ]
        expected = "OpenProgram Ink TUI PTY smoke passed"
    else:
        command = [str(node), str(entry), "--probe"]
        expected = "OpenProgram Ink TUI ready"
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0 or completed.stdout.strip() != expected:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"bundled Ink terminal UI probe failed: {detail}")
    return node_relative, entry_relative

def _probe_macos_window_control() -> None:
    if platform.system() != "Darwin":
        return
    for module in (
        "AppKit",
        "ApplicationServices",
        "Quartz",
        "ScreenCaptureKit",
    ):
        importlib.import_module(module)


def _office_assets():
    """The standard-library-only Office pack module, loaded by its path.

    Importing it as ``openprogram.programs.applications.office_editor.assets``
    would first import ``openprogram.programs``, which loads every Program.
    """
    import importlib.util
    name = "openprogram_office_assets"
    if name not in sys.modules:
        package = Path(importlib.util.find_spec("openprogram").origin).parent
        spec = importlib.util.spec_from_file_location(
            name, package / "programs" / "applications" / "office_editor" / "assets.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def _probe_office(root: Path) -> str:
    pack = _office_assets().validate_prepared_office_pack(root / "assets" / "office")
    if not pack.available:
        raise RuntimeError(f"prepared Office asset pack is unavailable: {pack.unavailable_reason}")
    return "assets/office"


def _probe(
    root: Path,
    product: dict,
    expected_openprogram_version: object,
    *,
    browser: bool = True,
    tui: Path | None = None,
) -> dict[str, dict[str, object]]:
    _verify_openprogram_version(expected_openprogram_version)
    if os.name != "nt":
        stable_python = _relative_file(
            root, "bin/python", "stable managed Python launcher"
        )
        if stable_python.resolve() != Path(sys.executable).resolve():
            raise RuntimeError(
                "stable managed Python launcher does not select this runtime"
            )
    playwright_root = _relative_dir(root, "assets/playwright", "Playwright data")

    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(playwright_root)
    _reject_excluded_runtime_wheels()

    importlib.import_module("openprogram")
    frontend = importlib.import_module("openprogram.interfaces.server.frontend")
    if not (frontend.out_dir() / "index.html").is_file():
        raise RuntimeError("prebuilt Web index is missing")
    for module in (
        "openprogram.models.providers",
        "openprogram.extensions.mcp",
        "openprogram.state.memory",
        "qrcode",
        "discord",
        "slack_sdk",
        "semble",
        "pypdf",
        "rich",
    ):
        importlib.import_module(module)

    _probe_pdf_tools()
    _probe_rich_terminal()
    _probe_ink_terminal(root, tui)

    _probe_macos_window_control()

    if (product.get("office") and not product["office"].get("optional")) or (root / "assets" / "office").is_dir():
        _probe_office(root)

    if browser:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser_executable = Path(playwright.chromium.executable_path).resolve()
            chromium = playwright.chromium.launch(headless=True)
            page = chromium.new_page()
            page.set_content("<title>OpenProgram runtime probe</title>")
            if page.title() != "OpenProgram runtime probe":
                raise RuntimeError("Playwright Chromium page probe failed")
            chromium.close()
        if not browser_executable.is_file():
            raise RuntimeError(f"Playwright Chromium is missing: {browser_executable}")
        try:
            browser_executable.relative_to(playwright_root.resolve())
        except ValueError as exc:
            raise RuntimeError("Playwright Chromium resolved outside the runtime") from exc

    result = {capability: {"present": True, "verified": browser or capability != "browser.playwright"}
              for capability in product["capabilities"]}
    return result


def _git_launch_environment(root: Path) -> dict[str, str]:
    """This process's environment with the bundled Git added the way launchers add it.

    A PATH that already names the bundled Git keeps its order, so a launcher
    that put another Git in front of it fails the check below.
    """
    git = root / "git"
    command_directory = git / ("cmd" if os.name == "nt" else "bin")
    environment = dict(os.environ)
    existing = environment.get("PATH", "")
    entries = [entry for entry in existing.split(os.pathsep) if entry]
    if command_directory not in {Path(entry).resolve() for entry in entries}:
        environment["PATH"] = os.pathsep.join([str(command_directory), *entries])
    if os.name != "nt":
        environment.setdefault("GIT_EXEC_PATH", str(git / "libexec" / "git-core"))
        environment.setdefault("GIT_TEMPLATE_DIR", str(git / "share" / "git-core" / "templates"))
    return environment


def _probe_git(root: Path) -> None:
    """The runtime carries the Git the product runs, and `git` selects it."""
    bundled = _relative_dir(root, "git", "bundled Git")
    environment = _git_launch_environment(root)
    found = shutil.which("git", path=environment["PATH"])
    if found is None or not Path(found).resolve().is_relative_to(bundled):
        raise RuntimeError(f"bundled Git is not first on PATH: git resolves to {found}")

    def run(*arguments: str) -> str:
        result = subprocess.run([found, *arguments], capture_output=True, text=True, env=environment, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(f"bundled Git does not run: {result.stderr.strip() or result.stdout.strip()}")
        return result.stdout.strip()

    if not run("--version").startswith("git version"):
        raise RuntimeError("bundled Git did not report a version")
    if not Path(run("--exec-path")).resolve().is_relative_to(bundled):
        raise RuntimeError("bundled Git runs helper programs from outside the runtime")
    with tempfile.TemporaryDirectory(prefix="openprogram-git-probe-") as temp_dir:
        run("init", "--quiet", temp_dir)
        if not (Path(temp_dir) / ".git" / "HEAD").is_file():
            raise RuntimeError("bundled Git could not create a repository")


def _probe_compiled_import() -> None:
    """A compiled package loads through this Python, signed or built on this machine.

    A signed macOS Python refuses a module its signature does not vouch for,
    unless library validation is off. Packages built on the user's machine are
    only ever ad-hoc signed, so one is reproduced here.
    """
    module = importlib.import_module("_cffi_backend")
    compiled = Path(getattr(module, "__file__", "") or "")
    if compiled.suffix not in {".so", ".pyd"}:
        raise RuntimeError(f"expected a compiled package, got: {compiled}")
    if platform.system() != "Darwin":
        return
    with tempfile.TemporaryDirectory(prefix="openprogram-import-probe-") as temp_dir:
        copy = Path(temp_dir) / compiled.name
        shutil.copy2(compiled, copy)
        subprocess.run(["codesign", "--force", "--sign", "-", "--timestamp=none", str(copy)],
                       check=True, capture_output=True, timeout=30)
        source = (f"import sys; sys.path.insert(0, {temp_dir!r}); import _cffi_backend as m; "
                  f"assert m.__file__.startswith({temp_dir!r}), m.__file__")
        result = subprocess.run([sys.executable, "-I", "-B", "-c", source],
                                capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(f"this Python refuses a module built on this machine: {result.stderr.strip()}")


# Load commands that name another image to load (LC_LOAD_DYLIB and kin).
_MACHO_LOADS = {0xC, 0x20, 0x80000018, 0x8000001F, 0x80000023}
_MACHO_RPATH, _MACHO_BUILD_VERSION, _MACHO_VERSION_MIN = 0x8000001C, 0x32, 0x24
_MACHO_SYSTEM = ("/usr/lib/", "/System/Library/")
_ELF_SYSTEM = ("/lib/", "/lib64/", "/usr/lib/", "/usr/lib64/")


def _macho_images(stream) -> list[int]:
    """Offsets of the Mach-O images in a thin or fat file; none for anything else."""
    stream.seek(0)
    head = stream.read(8)
    if head[:4] in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe"):
        return [0]
    if head[:4] not in (b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"):
        return []
    count = int.from_bytes(head[4:8], "big")
    if not 0 < count <= 30:  # a Java class file shares the fat magic
        return []
    wide = head[:4] == b"\xca\xfe\xba\xbf"
    entry = 32 if wide else 20
    table = stream.read(count * entry)
    if wide:
        return [int.from_bytes(table[i * entry + 8:i * entry + 16], "big") for i in range(count)]
    return [int.from_bytes(table[i * entry + 8:i * entry + 12], "big") for i in range(count)]


def _macho_commands(stream, offset: int) -> tuple[int, list[tuple[int, bytes]]]:
    stream.seek(offset)
    header = stream.read(32)
    if header[:4] not in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe"):
        return 0, []
    filetype = int.from_bytes(header[12:16], "little")
    count = int.from_bytes(header[16:20], "little")
    size = int.from_bytes(header[20:24], "little")
    stream.seek(offset + (32 if header[:4] == b"\xcf\xfa\xed\xfe" else 28))
    data = stream.read(size)
    commands, position = [], 0
    for _ in range(count):
        command = int.from_bytes(data[position:position + 4], "little")
        length = int.from_bytes(data[position + 4:position + 8], "little")
        if length < 8:
            break
        commands.append((command, data[position:position + length]))
        position += length
    return filetype, commands


def _command_string(body: bytes) -> str:
    start = int.from_bytes(body[8:12], "little")
    return body[start:].split(b"\0", 1)[0].decode("utf-8", "replace")


def _version(value: int) -> tuple[int, int]:
    return value >> 16, (value >> 8) & 0xFF


def _macho_problems(root: Path, path: Path, stream, floor: tuple[int, int]) -> list[str]:
    problems = []
    relative = path.relative_to(root)
    for offset in _macho_images(stream):
        filetype, commands = _macho_commands(stream, offset)
        if filetype not in (2, 6, 8):  # executables, libraries and loadable modules
            continue
        rpaths = [_command_string(body) for command, body in commands if command == _MACHO_RPATH]
        for command, body in commands:
            if command in (_MACHO_BUILD_VERSION, _MACHO_VERSION_MIN):
                is_macos = command == _MACHO_VERSION_MIN or int.from_bytes(body[8:12], "little") == 1
                minimum = _version(int.from_bytes(body[12:16] if command == _MACHO_BUILD_VERSION else body[8:12], "little"))
                if is_macos and minimum > floor:
                    problems.append(f"{relative} needs macOS {minimum[0]}.{minimum[1]}, "
                                    f"newer than the supported {floor[0]}.{floor[1]}")
            if command not in _MACHO_LOADS:
                continue
            name = _command_string(body)
            if name.startswith("/") and not name.startswith(_MACHO_SYSTEM):
                problems.append(f"{relative} loads {name}, outside the runtime and the system")
            elif name.startswith("@rpath/"):
                tail = name.removeprefix("@rpath/")
                for rpath in rpaths:
                    if (rpath.startswith("/") and not rpath.startswith(_MACHO_SYSTEM)
                            and not Path(rpath).resolve().is_relative_to(root)
                            and (Path(rpath) / tail).exists()):
                        problems.append(f"{relative} loads {name} from {rpath}, outside the runtime")
        if problems:
            break
    return problems


def _elf_problems(root: Path, path: Path, stream) -> list[str]:
    stream.seek(0)
    header = stream.read(64)
    if header[:4] != b"\x7fELF" or header[4] not in (1, 2) or header[5] not in (1, 2):
        return []
    wide, order = header[4] == 2, ("little" if header[5] == 1 else "big")

    def number(data: bytes, start: int, size: int) -> int:
        return int.from_bytes(data[start:start + size], order)

    if number(header, 16, 2) not in (2, 3):  # executables and shared objects
        return []
    word = 8 if wide else 4
    table, entry_size, entries = (number(header, 32, 8), number(header, 54, 2), number(header, 56, 2)) if wide \
        else (number(header, 28, 4), number(header, 42, 2), number(header, 44, 2))
    stream.seek(table)
    raw = stream.read(entry_size * entries)
    loads, dynamic = [], None
    for index in range(entries):
        item = raw[index * entry_size:(index + 1) * entry_size]
        kind = number(item, 0, 4)
        if wide:
            offset, address, size = number(item, 8, 8), number(item, 16, 8), number(item, 32, 8)
        else:
            offset, address, size = number(item, 4, 4), number(item, 8, 4), number(item, 16, 4)
        if kind == 1:
            loads.append((address, offset, size))
        elif kind == 2:
            dynamic = (offset, size)
    if dynamic is None:
        return []
    stream.seek(dynamic[0])
    section = stream.read(dynamic[1])
    tags: list[tuple[int, int]] = []
    for position in range(0, len(section) - 2 * word + 1, 2 * word):
        tag, value = number(section, position, word), number(section, position + word, word)
        if tag == 0:
            break
        tags.append((tag, value))
    strtab = next((value for tag, value in tags if tag == 5), None)
    file_offset = next((offset + strtab - address for address, offset, size in loads
                        if strtab is not None and address <= strtab < address + size), None)
    if file_offset is None:
        return []

    def string(index: int) -> str:
        stream.seek(file_offset + index)
        return stream.read(4096).split(b"\0", 1)[0].decode("utf-8", "replace")

    needed = [string(value) for tag, value in tags if tag == 1]
    search = [entry for tag, value in tags if tag in (15, 29) for entry in string(value).split(":") if entry]
    relative = path.relative_to(root)
    problems = [f"{relative} loads {name}, outside the runtime and the system"
                for name in needed if name.startswith("/") and not name.startswith(_ELF_SYSTEM)]
    for directory in search:
        if (not directory.startswith("/") or directory.startswith(_ELF_SYSTEM)
                or Path(directory).resolve().is_relative_to(root)):
            continue
        problems += [f"{relative} loads {name} from {directory}, outside the runtime"
                     for name in needed if (Path(directory) / name).exists()]
    return problems


def _native_problems(root: Path, macos_minimum: str) -> list[str]:
    """Native files that would not load on a user's machine as they did here.

    A file must load only from the runtime itself or the operating system, and
    a macOS one must run on the oldest macOS the release supports. Checked
    while building, on the machine that built the runtime, where a library
    leaked from the build machine (Homebrew's OpenSSL, say) still exists.
    """
    root = root.resolve()
    floor = tuple(int(part) for part in macos_minimum.split("."))[:2]
    problems: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open("rb") as stream:
            magic = stream.read(4)
            if magic == b"\x7fELF":
                problems += _elf_problems(root, path, stream)
            elif magic in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"):
                problems += _macho_problems(root, path, stream, floor)
    return problems


def _check_native_files(root: Path, product: dict, *, this_machine: bool = False) -> None:
    minimum = product["macosMinimum"]
    if this_machine and sys.platform == "darwin":
        # A runtime assembled in place from this machine's own packages serves
        # only this machine, so the oldest macOS it must load on is this one.
        import platform

        here = platform.mac_ver()[0]
        if here and tuple(int(part) for part in here.split(".")[:2]) > tuple(
            int(part) for part in minimum.split(".")[:2]
        ):
            minimum = here
    problems = _native_problems(root, minimum)
    if problems:
        raise RuntimeError("native files that would not load on a supported machine:\n  "
                           + "\n  ".join(problems[:40]))


def _read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("runtime_root", type=Path)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--python-relative")
    parser.add_argument("--openprogram-version")
    parser.add_argument("--uv-version")
    parser.add_argument("--defer-browser", action="store_true")
    parser.add_argument("--allow-deferred-browser", action="store_true")
    parser.add_argument("--probe-only", action="store_true",
                        help="Probe a base through a version's venv; write and require no manifest")
    parser.add_argument("--tui", type=Path, help="The version's terminal UI bundle")
    parser.add_argument("--require-git", action="store_true",
                        help="With --write: fail unless this runtime carries its own Git")
    parser.add_argument("--this-machine", action="store_true",
                        help="The runtime was assembled in place for this machine only")
    parser.add_argument("--compiled-import-only", action="store_true",
                        help="Check only that this Python loads compiled packages, for use after signing")
    args = parser.parse_args()

    root = args.runtime_root.resolve()
    product_path = root / "product-runtime.json"
    lock_path = root / "product-uv.lock"
    product = _read_json(product_path)
    if product.get("schema") != 1:
        raise RuntimeError("unsupported product manifest schema")

    if args.compiled_import_only:
        _probe_compiled_import()
        print(f"verified compiled packages load through {sys.executable}")
        return 0

    if args.probe_only:
        if not args.openprogram_version or args.tui is None:
            parser.error("--probe-only requires --openprogram-version and --tui")
        _probe_git(root)
        _check_native_files(root, product)
        _probe(root, product, args.openprogram_version, tui=args.tui.resolve())
        print(f"verified base through this version's environment: {root}")
        return 0

    # The runtime's own interpreter runs this, so its architecture is the
    # runtime's, which under emulation is not the machine's.
    from openprogram.host._platform.architecture import interpreter_architecture

    manifest_path = root / "runtime-manifest.json"
    if args.write:
        if not all(
            (args.python_relative, args.openprogram_version, args.uv_version)
        ):
            parser.error("--write requires Python, OpenProgram, and uv versions")
        _relative_file(root, args.python_relative, "managed Python")
        if args.allow_deferred_browser:
            parser.error("--allow-deferred-browser is only valid while checking an artifact")
        # A release build always requires Git. An installed runtime refreshed in
        # place predates it and keeps using the machine's Git until it is rebuilt.
        bundled_git = args.require_git or (root / "git").is_dir()
        if bundled_git:
            _probe_git(root)
        _check_native_files(root, product, this_machine=args.this_machine)
        capabilities = _probe(root, product, args.openprogram_version, browser=not args.defer_browser)
        node_relative = "bin/node.exe" if os.name == "nt" else "bin/node"
        tui_relative = "assets/tui/index.cjs"
        manifest = {
            "schema": 2,
            "openprogram": args.openprogram_version,
            "python": args.python_relative,
            "python_request": product["python"],
            "uv": args.uv_version,
            "platform": platform.system().lower(),
            "architecture": interpreter_architecture(),
            "product_manifest_sha256": hashlib.sha256(
                product_path.read_bytes()
            ).hexdigest(),
            "product_lock_sha256": hashlib.sha256(lock_path.read_bytes()).hexdigest(),
            "capabilities": capabilities,
            "browser_probe": "deferred" if args.defer_browser else "complete",
            "distributions": {
                distribution.metadata["Name"]: distribution.version
                for distribution in importlib.metadata.distributions()
                if distribution.metadata["Name"]
            },
            "assets": {
                "playwright": "assets/playwright",
                "node": node_relative,
                "tui": tui_relative,
            },
        }
        if bundled_git:
            manifest["assets"]["git"] = "git"
        if (root / "assets" / "office").is_dir():
            manifest["assets"]["office"] = "assets/office"
        worker_relative = "OpenProgram.app/Contents/MacOS/OpenProgram"
        if platform.system() == "Darwin" and (root / worker_relative).is_file():
            manifest["worker_python"] = worker_relative
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    else:
        manifest = _read_json(manifest_path)
        if manifest.get("schema") != 2:
            raise RuntimeError("unsupported runtime manifest schema")
        if manifest.get("worker_python"):
            helper = _relative_file(root, manifest["worker_python"], "named worker runtime")
            subprocess.run([str(helper), "-I", "-B", "-c", "import openprogram"], check=True, timeout=20)
        if manifest.get("platform") != platform.system().lower():
            raise RuntimeError("runtime platform does not match this machine")
        if manifest.get("architecture") != interpreter_architecture():
            raise RuntimeError("runtime architecture does not match this machine")
        _relative_file(root, manifest.get("python", ""), "managed Python")
        digest = hashlib.sha256(product_path.read_bytes()).hexdigest()
        if manifest.get("product_manifest_sha256") != digest:
            raise RuntimeError("product manifest hash mismatch")
        lock_digest = hashlib.sha256(lock_path.read_bytes()).hexdigest()
        if manifest.get("product_lock_sha256") != lock_digest:
            raise RuntimeError("product dependency lock hash mismatch")
        if (manifest.get("assets") or {}).get("git"):
            _probe_git(root)
        declared_office = (manifest.get("assets") or {}).get("office")
        if declared_office and not (root / declared_office).is_dir():
            raise RuntimeError("declared Office asset pack is missing")
        if declared_office:
            _probe_office(root)
        expected = set(product["capabilities"])
        actual = manifest.get("capabilities", {})
        complete = {"present": True, "verified": True}
        deferred = {"present": True, "verified": False}
        if (set(actual) != expected
                or any(value != complete for key, value in actual.items() if key != "browser.playwright")
                or actual.get("browser.playwright") not in ((complete, deferred) if args.allow_deferred_browser else (complete,))
                or manifest.get("browser_probe", "complete") != ("deferred" if actual.get("browser.playwright") == deferred else "complete")):
            raise RuntimeError("runtime capability manifest is incomplete")
        if args.defer_browser:
            parser.error("--defer-browser is only valid while writing an artifact")
        _probe(root, product, manifest.get("openprogram"), browser=not args.allow_deferred_browser)

    print(f"verified complete OpenProgram runtime: {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

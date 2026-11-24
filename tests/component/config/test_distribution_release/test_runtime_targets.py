"""The release runtime is built for its users' machines, not the build runner.

Wheels are picked for the oldest supported system and only as wheels; a native
file that loads a library from outside the runtime and the system (a runner's
Homebrew OpenSSL), or that needs a newer macOS than the documented minimum,
fails the build; and the App, the docs and the build agree on that minimum.
"""
from __future__ import annotations

import json
import re
import runpy
import struct
import tomllib

import pytest

from ._support import ROOT, Path

RELEASE = ROOT / "scripts" / "release"
PRODUCT = json.loads((RELEASE / "product-runtime.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def wheels() -> dict:
    return runpy.run_path(str(RELEASE / "runtime-wheels.py"))


@pytest.fixture(scope="module")
def verifier() -> dict:
    return runpy.run_path(str(RELEASE / "verify-product-runtime.py"))


# --- wheel selection -------------------------------------------------------

def test_every_target_installs_wheels_for_its_oldest_system(wheels) -> None:
    assert wheels["uv_args"]("macos-arm64", PRODUCT) == [
        "--python-platform", "aarch64-apple-darwin", "--only-binary", ":all:"]
    assert wheels["uv_args"]("linux-x86_64", PRODUCT)[:2] == [
        "--python-platform", "x86_64-manylinux_2_35"]
    intel = wheels["uv_args"]("macos-x86_64", PRODUCT)
    assert intel[intel.index("--only-binary") + 1] == ":all:"
    assert intel[intel.index("--no-binary") + 1] == "cryptography"


def test_the_lock_has_a_wheel_for_every_package_on_every_target(wheels) -> None:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    problems = [problem for target in wheels["TARGETS"] for problem in wheels["missing_wheels"](lock, target, PRODUCT)]
    assert problems == []


def _lock(*wheel_names: str) -> dict:
    return {"package": [
        {"name": "openprogram", "version": "1", "source": {"editable": "."},
         "dependencies": [{"name": "native"}], "optional-dependencies": {"all": [], "search": []}},
        {"name": "native", "version": "2",
         "wheels": [{"url": f"https://files/{name}"} for name in wheel_names]},
    ]}


def test_a_package_without_a_wheel_for_the_oldest_macos_is_reported(wheels) -> None:
    config = PRODUCT | {"sourceBuilds": {}}
    # Only a wheel for the runner's newer macOS: the old build silently took it.
    newer_only = _lock("native-2-cp312-cp312-macosx_15_0_arm64.whl")
    assert wheels["missing_wheels"](newer_only, "macos-arm64", config) == [
        f"macos-arm64: native 2 has no wheel for Python 3.12 on macOS {PRODUCT['macosMinimum']}"]
    # Only an Apple Silicon wheel: the Intel build compiled it from source.
    arm_only = _lock("native-2-cp311-abi3-macosx_11_0_arm64.whl")
    assert wheels["missing_wheels"](arm_only, "macos-arm64", config) == []
    assert len(wheels["missing_wheels"](arm_only, "macos-x86_64", config)) == 1
    universal = _lock("native-2-cp312-cp312-macosx_10_13_universal2.whl")
    assert wheels["missing_wheels"](universal, "macos-x86_64", config) == []


def test_a_source_build_that_now_has_a_wheel_is_reported(wheels) -> None:
    lock = _lock("native-2-py3-none-any.whl")
    config = PRODUCT | {"sourceBuilds": {"linux-arm64": ["native", "gone"]}}
    assert wheels["missing_wheels"](lock, "linux-arm64", config) == [
        "linux-arm64: native 2 has a wheel now; take it out of sourceBuilds",
        "linux-arm64: sourceBuilds lists gone, which this target does not install",
    ]


def test_linux_wheels_newer_than_the_glibc_floor_are_reported(wheels) -> None:
    config = PRODUCT | {"sourceBuilds": {}}
    assert wheels["missing_wheels"](_lock("native-2-cp312-cp312-manylinux_2_39_x86_64.whl"), "linux-x86_64", config)
    assert not wheels["missing_wheels"](_lock("native-2-cp312-cp312-manylinux2014_x86_64.whl"), "linux-x86_64", config)


def test_the_build_installs_through_the_target_arguments() -> None:
    script = (RELEASE / "build-base.sh").read_text(encoding="utf-8")
    assert "runtime-wheels.py\" uv-args" in script
    assert 'MACOSX_DEPLOYMENT_TARGET="$(read_pin macosMinimum)"' in script
    assert '"${target_args[@]}"' in script
    windows = (RELEASE / "build-base.ps1").read_text(encoding="utf-8")
    assert "runtime-wheels.py\") uv-args" in windows and "@TargetArgs" in windows


def test_the_intel_mac_legs_build_cryptography_against_static_openssl() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    prepare = "run: scripts/release/prepare-macos-native-deps.sh"
    # The runtime leg and the seed leg both build an Intel Mac base.
    assert workflow.count(prepare) == 2
    assert workflow.index(prepare) < workflow.index("scripts/release/build-product-runtime.sh")
    assert "runtime-wheels.py check" in workflow
    script = (RELEASE / "prepare-macos-native-deps.sh").read_text(encoding="utf-8")
    assert "VCPKG_LIBRARY_LINKAGE static" in script and "OPENSSL_STATIC=1" in script
    assert "VCPKG_OSX_DEPLOYMENT_TARGET $minimum" in script


# --- native files ----------------------------------------------------------

def _macho(*commands: bytes, filetype: int = 6) -> bytes:
    body = b"".join(commands)
    return struct.pack("<IiiIIIII", 0xFEEDFACF, 0x0100000C, 0, filetype, len(commands), len(body), 0, 0) + body


def _padded(fixed: bytes, text: str) -> bytes:
    data = fixed + text.encode() + b"\0"
    return data + b"\0" * (-len(data) % 8)


def _load(name: str) -> bytes:
    size = len(_padded(b"\0" * 24, name))
    return _padded(struct.pack("<IIIIII", 0xC, size, 24, 2, 0, 0), name)


def _rpath(path: str) -> bytes:
    size = len(_padded(b"\0" * 12, path))
    return _padded(struct.pack("<III", 0x8000001C, size, 12), path)


def _minimum(major: int, minor: int) -> bytes:
    return struct.pack("<IIIIII", 0x32, 24, 1, (major << 16) | (minor << 8), 0, 0)


def test_a_library_linked_to_the_build_machine_fails_the_build(verifier, tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    module = runtime / "python" / "lib" / "site-packages" / "cryptography" / "hazmat" / "bindings" / "_rust.abi3.so"
    module.parent.mkdir(parents=True)
    module.write_bytes(_macho(_minimum(11, 0), _load("/usr/local/opt/openssl@3/lib/libssl.3.dylib"),
                              _load("/usr/lib/libSystem.B.dylib"), _load("@rpath/libffi.8.dylib")))
    problems = verifier["_native_problems"](runtime, "13.5")
    assert problems == [f"{module.relative_to(runtime)} loads /usr/local/opt/openssl@3/lib/libssl.3.dylib, "
                        "outside the runtime and the system"]


def test_an_rpath_that_finds_a_library_outside_the_runtime_fails(verifier, tmp_path: Path) -> None:
    homebrew = tmp_path / "homebrew" / "lib"
    homebrew.mkdir(parents=True)
    (homebrew / "libssl.3.dylib").write_bytes(b"")
    runtime = tmp_path / "runtime"
    (runtime / "lib").mkdir(parents=True)
    (runtime / "lib" / "libffi.8.dylib").write_bytes(_macho(_minimum(11, 0)))
    module = runtime / "lib" / "native.so"
    # Build-time leftovers that exist nowhere stay harmless (sherpa-onnx ships some).
    module.write_bytes(_macho(_minimum(11, 0), _rpath("@loader_path"), _rpath("/Users/runner/work/build/lib"),
                              _rpath(str(homebrew)), _load("@rpath/libffi.8.dylib"), _load("@rpath/libssl.3.dylib"),
                              filetype=8))
    assert verifier["_native_problems"](runtime, "13.5") == [
        f"lib/native.so loads @rpath/libssl.3.dylib from {homebrew}, outside the runtime"]


def test_a_file_newer_than_the_supported_macos_fails_the_build(verifier, tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "orjson.so").write_bytes(_macho(_minimum(15, 0), _load("/usr/lib/libSystem.B.dylib"), filetype=8))
    (runtime / "node").write_bytes(_macho(_minimum(13, 5), filetype=2))
    (runtime / "Example.class").write_bytes(bytes.fromhex("cafebabe00000041") + b"\0" * 32)
    (runtime / "object.o").write_bytes(_macho(_minimum(26, 0), filetype=1))
    # Two slices in one file, the second too new.
    thin, newer = _macho(_minimum(11, 0)), _macho(_minimum(14, 0))
    fat = struct.pack(">II", 0xCAFEBABE, 2) + struct.pack(">5I", 0x0100000C, 0, 64, len(thin), 3) \
        + struct.pack(">5I", 0x01000007, 3, 64 + len(thin), len(newer), 3)
    (runtime / "fat.dylib").write_bytes(fat + b"\0" * (64 - len(fat)) + thin + newer)
    assert verifier["_native_problems"](runtime, "13.5") == [
        "fat.dylib needs macOS 14.0, newer than the supported 13.5",
        "orjson.so needs macOS 15.0, newer than the supported 13.5",
    ]


def _elf(needed: list[str], runpath: str) -> bytes:
    strings = b"\0"
    offsets = []
    for text in [*needed, runpath]:
        offsets.append(len(strings))
        strings += text.encode() + b"\0"
    dynamic_offset = 64 + 2 * 56
    tags = [(1, offset) for offset in offsets[:-1]] + [(29, offsets[-1])]
    strtab = dynamic_offset + (len(tags) + 2) * 16
    tags += [(5, strtab), (0, 0)]
    dynamic = b"".join(struct.pack("<QQ", tag, value) for tag, value in tags)
    total = strtab + len(strings)
    header = b"\x7fELF" + bytes([2, 1, 1]) + b"\0" * 9 + struct.pack(
        "<HHIQQQIHHHHHH", 3, 62, 1, 0, 64, 0, 0, 64, 56, 2, 0, 0, 0)
    load = struct.pack("<IIQQQQQQ", 1, 5, 0, 0, 0, total, total, 0x1000)
    dyn = struct.pack("<IIQQQQQQ", 2, 6, dynamic_offset, dynamic_offset, dynamic_offset, len(dynamic), len(dynamic), 8)
    return header + load + dyn + dynamic + strings


def test_a_linux_library_found_outside_the_runtime_fails_the_build(verifier, tmp_path: Path) -> None:
    linuxbrew = tmp_path / "linuxbrew" / "lib"
    linuxbrew.mkdir(parents=True)
    (linuxbrew / "libssl.so.3").write_bytes(b"")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "_rust.abi3.so").write_bytes(_elf(["libssl.so.3", "libc.so.6"], f"$ORIGIN/../lib:{linuxbrew}"))
    (runtime / "clean.so").write_bytes(_elf(["libc.so.6"], "$ORIGIN:/usr/lib/x86_64-linux-gnu"))
    (runtime / "absolute.so").write_bytes(_elf(["/opt/vendor/libfoo.so.1"], "$ORIGIN"))
    assert verifier["_native_problems"](runtime, "13.5") == [
        f"_rust.abi3.so loads libssl.so.3 from {linuxbrew}, outside the runtime",
        "absolute.so loads /opt/vendor/libfoo.so.1, outside the runtime and the system",
    ]


def test_the_build_checks_native_files_before_writing_the_manifest() -> None:
    source = (RELEASE / "verify-product-runtime.py").read_text(encoding="utf-8")
    write = source[source.index("    if args.write:"):source.index("        manifest = {")]
    assert "_check_native_files(root, product)" in write
    probe = source[source.index("    if args.probe_only:"):source.index('    manifest_path = root')]
    assert "_check_native_files(root, product)" in probe


# --- the advertised minimum ------------------------------------------------

def test_the_app_the_docs_and_the_build_name_the_same_oldest_macos() -> None:
    minimum = PRODUCT["macosMinimum"]
    package = json.loads((ROOT / "apps" / "desktop" / "package.json").read_text(encoding="utf-8"))
    assert package["build"]["mac"]["minimumSystemVersion"] == minimum
    english = (ROOT / "docs" / "install" / "install.html").read_text(encoding="utf-8")
    chinese = (ROOT / "docs" / "install" / "install.zh.html").read_text(encoding="utf-8")
    assert f"macOS {minimum} or later" in english
    assert f"macOS {minimum} 或更高" in chinese
    assert not re.search(r"macOS 13 (or later|或更高)", english + chinese)
    glibc = PRODUCT["glibcMinimum"]
    assert f"glibc {glibc} or later" in english
    installer = (RELEASE / "install-release.sh").read_text(encoding="utf-8")
    assert f'macos_minimum="{minimum}"' in installer


def _terminal_install_on_macos(tmp_path: Path, version: str):
    import os
    import subprocess

    host = tmp_path / f"mac-{version}"
    host.mkdir()
    for name, body in (("uname", 'case "$1" in -m) echo arm64 ;; *) echo Darwin ;; esac'),
                       ("sw_vers", f"echo {version}")):
        (host / name).write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        (host / name).chmod(0o755)
    return subprocess.run(
        ["sh", str(RELEASE / "install-release.sh")], check=False, capture_output=True, text=True,
        env=os.environ | {
            "PATH": f"{host}:/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(tmp_path / "home"),
            "OPENPROGRAM_VERSION": "1.3.0",
            "OPENPROGRAM_STATE_DIR": str(tmp_path / "state"),
            "OPENPROGRAM_BIN_DIR": str(tmp_path / "bin"),
            # Absent on purpose: a Mac that passes the check stops here, offline.
            "OPENPROGRAM_RUNTIME_ARCHIVE": str(tmp_path / "absent.tar.gz"),
        },
    )


@pytest.mark.skipif(__import__("sys").platform == "win32", reason="the POSIX shell installer")
def test_the_terminal_install_tells_an_older_mac_to_update_first(tmp_path: Path) -> None:
    """The browser driver needs macOS 13.5; on 13.4 the install says so instead of failing in dyld."""
    older = _terminal_install_on_macos(tmp_path, "13.4.1")
    assert older.returncode == 1
    assert f"OpenProgram needs macOS {PRODUCT['macosMinimum']} or later, and this Mac runs macOS 13.4.1." \
        in older.stderr
    assert "Software Update" in older.stderr
    for supported in ("13.5", "14", "26.1"):
        result = _terminal_install_on_macos(tmp_path, supported)
        assert "OpenProgram needs macOS" not in result.stderr, supported
        assert "OPENPROGRAM_RUNTIME_ARCHIVE not found" in result.stderr, result.stderr


def test_the_signed_app_smoke_loads_an_unsigned_module_through_both_worker_interpreters() -> None:
    smoke = (RELEASE / "smoke-packaged-runtime.sh").read_text(encoding="utf-8")
    full_layout = smoke[smoke.index('manifest="$resources/runtime/runtime-manifest.json"'):]
    assert full_layout.count("--compiled-import-only") == 2
    assert '"$resources/runtime/$worker_helper" -I -B' in full_layout

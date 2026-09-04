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


def _probe(
    root: Path,
    product: dict,
    expected_openprogram_version: object,
    *,
    browser: bool = True,
) -> dict[str, dict[str, object]]:
    _verify_openprogram_version(expected_openprogram_version)
    assets = {
        "playwright": "assets/playwright",
        "gpa_detector": "assets/gpa/model.pt",
    }
    playwright_root = _relative_dir(root, assets["playwright"], "Playwright data")
    gpa_model = _relative_file(root, assets["gpa_detector"], "GPA detector")
    if gpa_model.stat().st_size == 0:
        raise RuntimeError("GPA detector model is empty")

    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(playwright_root)
    os.environ["GPA_MODEL_PATH"] = str(gpa_model)
    _reject_excluded_runtime_wheels()

    importlib.import_module("openprogram")
    frontend = importlib.import_module("openprogram.webui.frontend")
    if not (frontend.out_dir() / "index.html").is_file():
        raise RuntimeError("prebuilt Web index is missing")
    for module in (
        "openprogram.providers",
        "openprogram.mcp",
        "openprogram.memory",
        "qrcode",
        "discord",
        "slack_sdk",
        "semble",
        "pymupdf",
        "pypdf",
        "rich",
    ):
        importlib.import_module(module)

    _probe_pdf_tools()
    _probe_rich_terminal()
    _probe_macos_window_control()

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

    from openprogram.programs._programs import import_installed_programs

    registered = set(import_installed_programs())
    expected_programs = {
        "gui": "gui_agent",
        "research": "research_agent",
        "wiki": "wiki_agent",
    }
    for name, function in expected_programs.items():
        program = product["programs"][name]
        importlib.import_module(program["import"])
        if function not in registered:
            raise RuntimeError(f"first-party Program did not register: {function}")

    return {capability: {"present": True, "verified": browser or capability != "browser.playwright"}
            for capability in product["capabilities"]}


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
    args = parser.parse_args()

    root = args.runtime_root.resolve()
    product_path = root / "product-runtime.json"
    lock_path = root / "product-uv.lock"
    product = _read_json(product_path)
    if product.get("schema") != 1:
        raise RuntimeError("unsupported product manifest schema")

    manifest_path = root / "runtime-manifest.json"
    if args.write:
        if not all(
            (args.python_relative, args.openprogram_version, args.uv_version)
        ):
            parser.error("--write requires Python, OpenProgram, and uv versions")
        _relative_file(root, args.python_relative, "managed Python")
        if args.allow_deferred_browser:
            parser.error("--allow-deferred-browser is only valid while checking an artifact")
        capabilities = _probe(root, product, args.openprogram_version, browser=not args.defer_browser)
        manifest = {
            "schema": 2,
            "openprogram": args.openprogram_version,
            "python": args.python_relative,
            "python_request": product["python"],
            "uv": args.uv_version,
            "platform": platform.system().lower(),
            "architecture": platform.machine().lower(),
            "product_manifest_sha256": hashlib.sha256(
                product_path.read_bytes()
            ).hexdigest(),
            "product_lock_sha256": hashlib.sha256(lock_path.read_bytes()).hexdigest(),
            "capabilities": capabilities,
            "browser_probe": "deferred" if args.defer_browser else "complete",
            "programs": product["programs"],
            "distributions": {
                distribution.metadata["Name"]: distribution.version
                for distribution in importlib.metadata.distributions()
                if distribution.metadata["Name"]
            },
            "assets": {
                "playwright": "assets/playwright",
                "gpa_detector": "assets/gpa/model.pt",
            },
        }
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    else:
        manifest = _read_json(manifest_path)
        if manifest.get("schema") != 2:
            raise RuntimeError("unsupported runtime manifest schema")
        current_arch = platform.machine().lower()
        expected_arches = {
            "x86_64": {"x86_64", "amd64"},
            "amd64": {"x86_64", "amd64"},
            "arm64": {"arm64", "aarch64"},
            "aarch64": {"arm64", "aarch64"},
        }.get(current_arch, {current_arch})
        if manifest.get("platform") != platform.system().lower():
            raise RuntimeError("runtime platform does not match this machine")
        if manifest.get("architecture") not in expected_arches:
            raise RuntimeError("runtime architecture does not match this machine")
        _relative_file(root, manifest.get("python", ""), "managed Python")
        digest = hashlib.sha256(product_path.read_bytes()).hexdigest()
        if manifest.get("product_manifest_sha256") != digest:
            raise RuntimeError("product manifest hash mismatch")
        lock_digest = hashlib.sha256(lock_path.read_bytes()).hexdigest()
        if manifest.get("product_lock_sha256") != lock_digest:
            raise RuntimeError("product dependency lock hash mismatch")
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

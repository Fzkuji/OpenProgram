"""release runtime package tests."""
from __future__ import annotations
from ._support import (
    POSIX_SHELL_INTEGRATION,
    Path,
    ROOT,
    SimpleNamespace,
    _desktop_package,
    _fake_desktop_app,
    _fake_posix_cli_archive,
    importlib,
    json,
    os,
    platform,
    plistlib,
    pytest,
    re,
    runpy,
    subprocess,
    sys,
    zipfile,
)


def test_desktop_targets_and_embedded_runtime_are_declared() -> None:
    package = _desktop_package()
    build = package["build"]
    mac_targets = {
        target if isinstance(target, str) else target["target"]
        for target in build["mac"]["target"]
    }
    assert {"dmg", "zip"} <= mac_targets
    assert "linux" not in build
    assert "dist:linux" not in package["scripts"]
    assert {item["to"] for item in build["extraResources"]} >= {"runtime"}
    resources = {item["to"]: item["from"] for item in build["extraResources"]}
    assert resources["update/install-app.sh"] == "scripts/install-app.sh"
    assert "worker-recovery-state.js" in build["files"]
    assert "tab-transfer-validation.js" in build["files"]
    assert package["desktopName"] == "ai.openprogram.OpenProgram.desktop"



@POSIX_SHELL_INTEGRATION
def test_packaged_runtime_smoke_rejects_an_incomplete_app_before_install(
    tmp_path: Path,
) -> None:
    install_root = tmp_path / "installed"
    current = _fake_desktop_app(install_root / "Applications", "0.6.2")
    package_dir = tmp_path / "package"
    _fake_desktop_app(package_dir, "0.6.3")

    failed = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts" / "release" / "smoke-packaged-runtime.sh"),
            "mac",
            str(package_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    assert failed.returncode != 0
    with (current / "Contents" / "Info.plist").open("rb") as stream:
        assert plistlib.load(stream)["CFBundleShortVersionString"] == "0.6.2"



def test_macos_icon_uses_the_apple_icon_source_format() -> None:
    desktop = ROOT / "apps" / "desktop"
    package = json.loads((desktop / "package.json").read_text(encoding="utf-8"))
    icon_source = desktop / "build" / "AppIcon.icon"
    packaged_icon = desktop / "build" / "icon.icns"
    assert package["build"]["mac"]["icon"] == "build/icon.icns"
    assert (icon_source / "icon.json").is_file()
    assert packaged_icon.is_file()
    assert packaged_icon.read_bytes().startswith(b"icns")
    assert not (desktop / "build" / "icon.svg").exists()
    assert not (desktop / "build" / "icon.iconset").exists()



def test_core_agentic_functions_are_not_excluded_from_wheel() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'exclude = ["openprogram.programs.workflow.*"]' not in pyproject



def test_packaged_worker_uses_isolated_embedded_python() -> None:
    helper = (ROOT / "apps" / "desktop" / "packaged-runtime.js").read_text(encoding="utf-8")
    main = (ROOT / "apps" / "desktop" / "main.js").read_text(encoding="utf-8")
    assert '"-I", "-B", "-m", "openprogram", "worker", "start"' in helper
    assert "process.resourcesPath" in main
    assert "app.getVersion()" in main
    packaged_branch = re.search(
        r"if \(app\.isPackaged\)(.*?)(?:\n\s*else|\n\s*})",
        main,
        re.DOTALL,
    )
    assert packaged_branch is not None
    assert 'start("openprogram"' not in packaged_branch.group(1)
    assert "/opt/miniconda3" not in main
    assert 'env.OPENPROGRAM_IMMUTABLE_RUNTIME = "1"' in main



def test_packaged_runtime_rejects_program_mutation(monkeypatch, capsys) -> None:
    from openprogram.cli.commands.programs import _cmd_install, _cmd_uninstall

    monkeypatch.setenv("OPENPROGRAM_IMMUTABLE_RUNTIME", "1")
    with pytest.raises(SystemExit) as install_exit:
        _cmd_install("research")
    assert install_exit.value.code == 1
    assert "disabled in the packaged desktop runtime" in capsys.readouterr().out

    with pytest.raises(SystemExit) as uninstall_exit:
        _cmd_uninstall("research")
    assert uninstall_exit.value.code == 1
    assert "disabled in the packaged desktop runtime" in capsys.readouterr().out



@POSIX_SHELL_INTEGRATION
def test_failed_new_release_probe_is_cleanly_retryable(tmp_path: Path) -> None:
    failed_archive, failed_digest = _fake_posix_cli_archive(
        tmp_path / "failed", manifest_version="0.6.7", worker_start_exit=41
    )
    state = tmp_path / "state"
    launcher = tmp_path / "bin"
    common_env = os.environ | {
        "HOME": str(tmp_path / "home"),
        "OPENPROGRAM_VERSION": "0.6.7",
        "OPENPROGRAM_STATE_DIR": str(state),
        "OPENPROGRAM_BIN_DIR": str(launcher),
    }
    failed = subprocess.run(
        ["sh", str(ROOT / "scripts" / "release" / "install-release.sh")],
        check=False,
        env=common_env
        | {
            "OPENPROGRAM_RUNTIME_ARCHIVE": str(failed_archive),
            "OPENPROGRAM_RUNTIME_SHA256": failed_digest,
        },
        capture_output=True,
        text=True,
    )
    runtime_root = state / "runtime" / "cli"
    assert failed.returncode == 41
    assert not (runtime_root / "releases" / "0.6.7").exists()
    assert list(runtime_root.glob(".staging-*")) == []

    good_archive, good_digest = _fake_posix_cli_archive(
        tmp_path / "good", manifest_version="0.6.7"
    )
    subprocess.run(
        ["sh", str(ROOT / "scripts" / "release" / "install-release.sh")],
        check=True,
        env=common_env
        | {
            "OPENPROGRAM_RUNTIME_ARCHIVE": str(good_archive),
            "OPENPROGRAM_RUNTIME_SHA256": good_digest,
        },
        capture_output=True,
        text=True,
    )
    assert (runtime_root / "current").resolve() == (
        runtime_root / "releases" / "0.6.7"
    )



def test_cli_exposes_distribution_version(capsys) -> None:
    from openprogram.cli import build_parser

    with pytest.raises(SystemExit) as version_exit:
        build_parser().parse_args(["--version"])
    assert version_exit.value.code == 0
    assert capsys.readouterr().out.startswith("openprogram ")



def test_desktop_runtime_removes_absolute_python_aliases() -> None:
    staging = (ROOT / "scripts" / "release" / "build-product-runtime.sh").read_text(
        encoding="utf-8"
    )
    assert 'readlink "$python_alias"' in staging
    assert 'unlink "$python_alias"' in staging



def test_product_runtime_verifier_probes_macos_window_dependencies(
    monkeypatch,
) -> None:
    helper = runpy.run_path(
        str(ROOT / "scripts/release/verify-product-runtime.py")
    )
    imported: list[str] = []
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(importlib, "import_module", imported.append)

    helper["_probe_macos_window_control"]()

    assert imported == [
        "AppKit",
        "ApplicationServices",
        "Quartz",
        "ScreenCaptureKit",
    ]



@POSIX_SHELL_INTEGRATION
def test_release_version_verifier_rejects_a_mismatched_built_wheel(
    tmp_path: Path,
) -> None:
    source_version = _desktop_package()["version"]
    installed = _fake_desktop_app(tmp_path / "installed", source_version)
    wheel = tmp_path / "openprogram-0.6.1-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "openprogram-0.6.1.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: openprogram\nVersion: 0.6.1\n",
        )
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "release" / "verify-release-version.py"),
            "--installed-app",
            str(installed),
            "--require-source-match",
            "--wheel",
            str(wheel),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert f"wheel version 0.6.1 != source version {source_version}" in result.stderr



def test_product_runtime_installs_complete_default_capabilities() -> None:
    staging = (ROOT / "scripts" / "release" / "build-product-runtime.sh").read_text(
        encoding="utf-8"
    )
    verifier = (ROOT / "scripts" / "release" / "verify-product-runtime.py").read_text(
        encoding="utf-8"
    )
    product_config = (ROOT / "scripts" / "release" / "product-runtime.json").read_text(
        encoding="utf-8"
    )
    assert "--frozen --no-dev" in staging
    assert "--extra all --extra search" in staging
    assert "--extra embedding" not in staging
    assert "--require-hashes" in staging
    assert '--no-deps "$wheel"' in staging
    assert '--no-deps "$program_dir"' in staging
    assert '"${program_dir}[ocr]"' not in staging
    assert "playwright.sync_api" in verifier
    assert "playwright install chromium" in staging
    assert "easyocr.Reader" not in staging
    assert '"${program_dir}[pdf]"' in staging
    assert "https://download.pytorch.org/whl/cpu" not in staging
    assert "torch==$torch_version" not in staging
    assert "2147483648" in (
        ROOT / "scripts" / "release" / "archive-product-runtime.sh"
    ).read_text(encoding="utf-8")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    main_deps = pyproject.split("[project.optional-dependencies]")[0]
    assert "sentence-transformers" not in main_deps
    assert 'embedding = ["sentence-transformers>=3.4,<4"]' in pyproject
    assert '"pypdf>=5.0"' in pyproject
    assert '"rich>=13.0"' in pyproject
    assert '"sentence_transformers"' not in verifier
    assert "_reject_excluded_runtime_wheels()" in verifier
    assert "product runtime must not ship excluded distributions or wheels" in verifier
    assert '"pypdf",' in verifier
    assert "_probe_pdf_tools()" in verifier
    assert "_probe_rich_terminal()" in verifier
    assert "Salesforce/GPA-GUI-Detector" in product_config
    assert "GUI-Agent-Harness" in product_config
    assert "Research-Agent-Harness" in product_config
    assert "Wiki-Agent-Harness" in product_config



def test_posix_runtime_build_has_stable_python_and_bundled_tui_launchers() -> None:
    builder = (ROOT / "scripts" / "release" / "build-product-runtime.sh").read_text(
        encoding="utf-8"
    )
    assert "for command_name in npm node git" in builder
    assert 'ln -s "../$python_relative" "$runtime_root/bin/python"' in builder
    assert 'cp "$(command -v node)" "$runtime_root/bin/node"' in builder
    assert '"$runtime_root/assets/tui/index.cjs"' in builder
    assert '"$runtime_root/bin/smoke-ink-tui-pty.py"' in builder

    verifier = (ROOT / "scripts" / "release" / "verify-product-runtime.py").read_text(
        encoding="utf-8"
    )
    assert 'sys.platform.startswith("linux")' in verifier
    assert '"bin/smoke-ink-tui-pty.py"' in verifier



def test_product_runtime_pdf_tool_probe() -> None:
    verifier = runpy.run_path(str(ROOT / "scripts" / "release" / "verify-product-runtime.py"))
    verifier["_probe_pdf_tools"]()



def test_product_runtime_rich_terminal_probe() -> None:
    verifier = runpy.run_path(str(ROOT / "scripts" / "release" / "verify-product-runtime.py"))
    verifier["_probe_rich_terminal"]()



def test_immutable_runtime_doctor_does_not_require_node_or_npm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openprogram.cli.commands import doctor

    monkeypatch.setenv("OPENPROGRAM_IMMUTABLE_RUNTIME", "1")
    monkeypatch.setattr(doctor.shutil, "which", lambda _name: None)

    assert doctor._check_node() == (
        True,
        "node available",
        "not required in immutable product runtime",
    )
    assert doctor._check_npm() == (
        True,
        "npm available",
        "not required in immutable product runtime",
    )
    assert doctor._check_git() == (False, "git available", "not on PATH")



def test_packaged_cli_resolves_bundled_tui_without_source(tmp_path, monkeypatch) -> None:
    import tomllib
    import openprogram
    from openprogram.cli import ink as cli_ink

    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "dist/*.mjs" in config["tool"]["setuptools"]["package-data"]["openprogram_cli"]
    package = tmp_path / "openprogram_cli"
    entry = package / "dist" / "index.mjs"
    entry.parent.mkdir(parents=True)
    entry.write_text("// bundled terminal entry\n", encoding="utf-8")
    monkeypatch.setattr(openprogram, "__file__", str(tmp_path / "openprogram" / "__init__.py"))
    monkeypatch.setattr(cli_ink, "__file__", str(package / "_impl" / "ink.py"))
    assert cli_ink._resolve_cli_entry() == entry



def test_packaged_cli_falls_back_when_ink_runtime_is_absent(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from types import SimpleNamespace

    from openprogram.cli import chat as cli_chat
    from openprogram.cli import ink as cli_ink
    from openprogram.agent.management import manager

    monkeypatch.setenv("OPENPROGRAM_IMMUTABLE_RUNTIME", "1")
    monkeypatch.setattr(cli_ink, "_has_interactive_tui_stdio", lambda: True)
    monkeypatch.setattr(
        cli_ink,
        "_resolve_node",
        lambda: (_ for _ in ()).throw(RuntimeError("node unavailable")),
    )
    with pytest.raises(RuntimeError, match="node unavailable"):
        cli_ink.run_ink_tui()

    monkeypatch.setattr(
        cli_chat, "_get_chat_runtime", lambda: ("test", SimpleNamespace(model="test"))
    )
    monkeypatch.setattr(manager, "get_default", lambda: SimpleNamespace(id="main"))
    monkeypatch.setattr(
        cli_ink,
        "run_ink_tui",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("Ink unavailable")),
    )
    monkeypatch.setattr(cli_chat, "_print_banner", lambda *_args, **_kwargs: None)

    from rich.console import Console

    monkeypatch.setattr(
        Console,
        "input",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(EOFError()),
    )
    cli_chat.run_cli_chat(tui=True)
    output = capsys.readouterr()
    assert "falling back to REPL" in output.out
    assert "Goodbye" in output.out



def test_missing_bundled_pdf_dependency_requires_complete_reinstall(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import sys

    from openprogram.programs.tools.web.pdf import execute as pdf_extract
    from openprogram.programs.tools.files.read import _read_pdf

    pdf_path = tmp_path / "probe.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    monkeypatch.setitem(sys.modules, "pypdf", None)

    for result in (
        pdf_extract(file_path=str(pdf_path)),
        _read_pdf(str(pdf_path), offset=1, limit=1),
    ):
        assert "reinstall the complete OpenProgram release" in result
        assert "pip install" not in result



@pytest.mark.parametrize(
    "excluded_dist",
    [
        "easyocr",
        "opencv-contrib-python",
        "opencv-contrib-python-headless",
        "opencv-python",
        "opencv-python-headless",
        "torch",
        "torchvision",
        "sentence-transformers",
        "triton",
        "nvidia-cublas",
        "cuda-runtime",
        "opencv_python_headless",
    ],
)
def test_product_runtime_rejects_excluded_distributions(
    monkeypatch: pytest.MonkeyPatch,
    excluded_dist: str,
) -> None:
    verifier = runpy.run_path(str(ROOT / "scripts" / "release" / "verify-product-runtime.py"))

    class _Dist:
        def __init__(self, name: str) -> None:
            self.metadata = {"Name": name}

    monkeypatch.setattr(
        verifier["importlib"].metadata,
        "distributions",
        lambda: [_Dist(excluded_dist), _Dist("pypdf")],
    )
    with pytest.raises(RuntimeError, match="must not ship excluded distributions"):
        verifier["_reject_excluded_runtime_wheels"]()



def test_product_runtime_accepts_runtime_without_excluded_distributions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = runpy.run_path(str(ROOT / "scripts" / "release" / "verify-product-runtime.py"))

    class _Dist:
        def __init__(self, name: str) -> None:
            self.metadata = {"Name": name}

    monkeypatch.setattr(
        verifier["importlib"].metadata,
        "distributions",
        lambda: [_Dist("pypdf")],
    )
    verifier["_reject_excluded_runtime_wheels"]()



def test_product_runtime_rejects_installed_openprogram_version_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = runpy.run_path(str(ROOT / "scripts" / "release" / "verify-product-runtime.py"))
    verify_version = verifier["_verify_openprogram_version"]

    monkeypatch.setattr("importlib.metadata.version", lambda _name: "0.6.1")
    with pytest.raises(
        RuntimeError,
        match=r"OpenProgram version mismatch: expected 0\.6\.6, got 0\.6\.1",
    ):
        verify_version("0.6.6")



def test_search_runtime_dependency_supports_macos_x64() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    assert 'search = ["semble>=0.5.3"]' in pyproject
    assert "tree-sitter-language-pack" not in lock
    assert re.search(
        r"semble_grammars-[^-]+-py3-none-macosx_[^-]+_x86_64\.whl",
        lock,
    )



def test_memory_runtime_dependency_supports_macos_x64() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    assert "sys_platform == 'darwin' and platform_machine == 'x86_64'" in pyproject
    assert re.search(r"torch-[^-]+-.*macosx_[^-]+_x86_64\.whl", lock)



def test_product_manifest_requires_one_complete_capability_set() -> None:
    manifest = json.loads(
        (ROOT / "scripts" / "release" / "product-runtime.json").read_text(encoding="utf-8")
    )
    assert manifest["schema"] == 1
    assert set(manifest["capabilities"]) == {
        "web",
        "providers",
        "mcp",
        "memory",
        "channels",
        "search",
        "tui.ink",
        "browser.playwright",
        "model.gpa_detector",
        "program.gui",
        "program.research",
        "program.wiki",
    }
    assert set(manifest["programs"]) == {"gui", "research", "wiki"}
    assert "torch" not in manifest["programs"]["gui"]
    assert "torchvision" not in manifest["programs"]["gui"]
    assert "numpy" not in manifest["programs"]["gui"]
    assert "opencv" not in manifest["programs"]["gui"]
    for program in manifest["programs"].values():
        assert re.fullmatch(r"[0-9a-f]{40}", program["commit"])



def test_linux_complete_runtime_smoke_is_runnable_without_release_credentials() -> None:
    workflow = (ROOT / ".github" / "workflows" / "linux-release-smoke.yml").read_text(
        encoding="utf-8"
    )
    assert "workflow_dispatch:" in workflow
    assert "schedule:" in workflow
    assert "environment: release" not in workflow
    assert "ubuntu-24.04-arm" in workflow
    assert "scripts/install-release.sh" in workflow
    assert "smoke-linux-runtime-baseline.sh" in workflow
    assert "Ubuntu 22.04 glibc baseline" in workflow
    assert "AppImage" not in workflow
    assert "electron-builder" not in workflow

    release = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    assert "smoke-linux-runtime-baseline.sh" in release

    baseline = (
        ROOT / "scripts" / "release" / "smoke-linux-runtime-baseline.sh"
    ).read_text(encoding="utf-8")
    assert '"$runtime/bin/verify-product-runtime.py"' in baseline
    assert '"$runtime/assets/tui/index.cjs" --probe' not in baseline



def test_packaged_smoke_rejects_unreleased_linux_desktop() -> None:
    smoke = (ROOT / "scripts" / "release" / "smoke-packaged-runtime.sh").read_text(encoding="utf-8")
    assert "AppImage" not in smoke
    assert "linux)" not in smoke
    assert "python3 -c" not in smoke



@POSIX_SHELL_INTEGRATION
def test_packaged_smoke_reads_formatted_runtime_manifest(tmp_path: Path) -> None:
    runtime = (
        tmp_path
        / "dist"
        / "mac-arm64"
        / "OpenProgram.app"
        / "Contents"
        / "Resources"
        / "runtime"
    )
    runtime.mkdir(parents=True)
    (runtime / "runtime-manifest.json").write_text(
        json.dumps({"python": "python/bin/python3"}, indent=2) + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            str(ROOT / "scripts" / "release" / "smoke-packaged-runtime.sh"),
            "mac",
            str(tmp_path / "dist"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "managed Python is not executable" in result.stderr
    assert "managed Python path missing" not in result.stderr



def test_openclaw_source_checkout_uses_its_locked_environment() -> None:
    for relative_path in (
        "docs/integrations/openclaw.md",
        "docs/integrations/openclaw.zh.md",
    ):
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "uv sync --locked" in source
        assert "uv run --project ~/.openclaw/workspace/OpenProgram python" in source
        assert (
            "python3 ~/.openclaw/workspace/skills/my-agentic-skill/scripts/analyze.py"
            not in source
        )



def test_python_import_troubleshooting_distinguishes_managed_and_source() -> None:
    for relative_path in (
        "docs/server/troubleshooting.md",
        "docs/server/troubleshooting.zh.md",
    ):
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "uv sync --locked" in source
        assert "uv run --project /path/to/OpenProgram python" in source
        assert "./scripts/install.sh" not in source



def test_packaged_browser_install_does_not_modify_python_environment(
    monkeypatch, capsys
) -> None:
    from openprogram.cli.commands.browser import _cmd_browser_install

    monkeypatch.setenv("OPENPROGRAM_IMMUTABLE_RUNTIME", "1")
    monkeypatch.setattr(
        "openprogram.cli.commands.browser._pip_install",
        lambda _spec: (_ for _ in ()).throw(AssertionError("pip invoked")),
    )

    assert _cmd_browser_install("playwright") == 1
    output = capsys.readouterr().out.lower()
    assert "complete release" in output
    assert "source checkout" in output

    help_output = subprocess.run(
        [sys.executable, "-m", "openprogram", "browser", "install", "--help"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.lower()
    help_output = " ".join(help_output.split())
    assert "source checkout only" in help_output
    assert "packaged releases reject this command" in help_output



def test_release_manifest_records_hashes(tmp_path: Path) -> None:
    import subprocess

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "OpenProgram-0.6.4-mac-arm64.dmg").write_bytes(b"artifact")
    output = artifacts / "release-manifest.json"
    subprocess.run(
        [
            "python",
            str(ROOT / "scripts" / "release" / "create-release-manifest.py"),
            str(artifacts),
            "--version",
            "v0.6.4",
            "--output",
            str(output),
        ],
        check=True,
    )
    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["version"] == "0.6.4"
    assert manifest["files"][0]["sha256"] == (
        "c7c5c1d70c5dec4416ab6158afd0b223ef40c29b1dc1f97ed9428b94d4cadb1c"
    )



@POSIX_SHELL_INTEGRATION
@pytest.mark.parametrize("deferred", [False, True])
def test_packaged_smoke_passes_verifier_arguments_with_system_bash(
    tmp_path: Path, deferred: bool,
) -> None:
    runtime = tmp_path / "dist" / "OpenProgram.app" / "Contents" / "Resources" / "runtime"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "runtime-manifest.json").write_text(json.dumps({"python": "bin/python3"}, indent=2) + "\n")
    capture = tmp_path / "verifier-arguments"
    executable = runtime / "bin" / "python3"
    executable.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$CAPTURE"\nexit 37\n')
    executable.chmod(0o755)
    result = subprocess.run(
        ["/bin/bash", str(ROOT / "scripts/release/smoke-packaged-runtime.sh"),
         "mac", str(tmp_path / "dist")],
        env={**os.environ, "TMPDIR": str(tmp_path), "CAPTURE": str(capture),
             "OPENPROGRAM_SELF_UPDATE_DEFER_BROWSER": "1" if deferred else ""},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 37, result.stderr
    expected = ["-I", str(runtime / "bin/verify-product-runtime.py"), str(runtime)]
    if deferred:
        expected.append("--allow-deferred-browser")
    assert capture.read_text().splitlines() == expected



@pytest.mark.parametrize("symlink", [False, True])
def test_package_cli_preserves_legacy_location_for_upgrade_and_uninstall(tmp_path, monkeypatch, symlink):
    import openprogram
    import openprogram.paths as paths
    from openprogram.programs import _programs
    from openprogram.cli.commands.programs import _cmd_install, _cmd_uninstall

    package = tmp_path / "openprogram"
    root = package / "programs"
    old = root / "applications" / "research_harness"
    old.parent.mkdir(parents=True)
    (root / "packages").mkdir()
    target = tmp_path / "owned-checkout" if symlink else old
    target.mkdir()
    (target / ".git").mkdir()
    agentics = target / "research_harness" / "agentics"
    agentics.mkdir(parents=True)
    (agentics.parent / "__init__.py").write_text("")
    (agentics / "__init__.py").write_text("")
    if symlink:
        old.symlink_to(target, target_is_directory=True)
    monkeypatch.delenv("OPENPROGRAM_IMMUTABLE_RUNTIME", raising=False)
    monkeypatch.setattr(openprogram, "__file__", str(package / "__init__.py"))
    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    _programs.record_program_source(old, source="fixture", base=str(old.parent))
    calls = []
    monkeypatch.setattr(subprocess, "call", lambda args: calls.append(args) or 0)
    _cmd_install("research", upgrade=True)
    assert calls == ([] if symlink else [["git", "-C", str(old), "pull", "--ff-only"]])
    assert not (root / "packages" / "research_harness").exists()
    _cmd_uninstall("research")
    assert not old.exists()
    assert _programs.owner_controlled_program_sources() == []
    if symlink:
        assert (target / ".git").is_dir()


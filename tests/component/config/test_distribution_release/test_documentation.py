"""release documentation tests."""
from __future__ import annotations
from ._support import (
    POSIX_SHELL_INTEGRATION,
    ROOT,
    _desktop_package,
    os,
    subprocess,
)


def test_docs_publish_short_installer_at_the_domain_root() -> None:
    workflow = (ROOT / ".github" / "workflows" / "docs-pages.yml").read_text(
        encoding="utf-8"
    )
    assert "mv _publish/docs/install.sh _publish/install" in workflow



def test_normal_user_docs_use_the_short_release_installer() -> None:
    short_command = "curl -fsSL https://openprogram.io/install | sh"
    for relative in (
        "README.md",
        "docs/README.md",
        "docs/README.zh.md",
        "docs/install/install.md",
        "docs/install/install.zh.md",
        "docs/install/upgrade.md",
        "docs/install/upgrade.zh.md",
        "docs/start/GETTING_STARTED.md",
        "docs/start/GETTING_STARTED.zh.md",
        "website/index.html",
    ):
        contents = (ROOT / relative).read_text(encoding="utf-8")
        assert short_command in contents, relative
        assert "v0.6.1/scripts/install-release.sh" not in contents, relative



def test_release_frontend_staging_includes_prebuilt_docs() -> None:
    staging = (ROOT / "scripts" / "release" / "stage-release-assets.sh").read_text(
        encoding="utf-8"
    )
    assert "scripts.docs_site.build" in staging
    assert 'docs_target_dir="$target_dir/docs"' in staging
    assert 'cp -R "$docs_source_dir/." "$docs_target_dir/"' in staging
    copy = staging.index('cp -R "$source_dir/." "$target_dir/"')
    gate = staging[copy:]
    assert "chat.html" in gate
    assert 'aria-label="Authenticating"' in gate
    assert 'id="sidebar"' in gate



@POSIX_SHELL_INTEGRATION
def test_release_asset_staging_invokes_locked_docs_builder(tmp_path) -> None:
    repo = tmp_path / "repo"
    scripts = repo / "scripts"
    release_scripts = scripts / "release"
    fake_bin = tmp_path / "bin"
    release_scripts.mkdir(parents=True)
    fake_bin.mkdir()
    (repo / "apps" / "web").mkdir(parents=True)
    script = release_scripts / "stage-release-assets.sh"
    script.write_text(
        (ROOT / "scripts" / "release" / "stage-release-assets.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    script.chmod(0o755)

    # Office preparation has separate public install tests. This test isolates
    # the docs builder invocation while staging still calls its dependency.
    office = release_scripts / "office"
    office.mkdir()
    (office / "stage.py").write_text("print('fixture Office resources staged')\n")

    fake_npm = fake_bin / "npm"
    fake_npm.write_text(
        """#!/bin/sh
if [ "$1" = "run" ] && [ "$2" = "build" ]; then
  mkdir -p "$PWD/apps/web/out"
  printf '<html>web</html>\\n' > "$PWD/apps/web/out/index.html"
  printf '<body><div id="sidebar"></div></body>\\n' > "$PWD/apps/web/out/chat.html"
elif [ "$1" = "run" ] && [ "$2" = "build:release" ]; then
  mkdir -p "$PWD/apps/cli/python/openprogram_cli/dist"
  printf '// terminal bundle\\n' > "$PWD/apps/cli/python/openprogram_cli/dist/index.mjs"
fi
if [ "$1" = "run" ] && [ "$2" = "build:standalone" ]; then
  mkdir -p "$PWD/apps/cli/dist"
  printf 'standalone\\n' > "$PWD/apps/cli/dist/index-standalone.cjs"
fi
""",
        encoding="utf-8",
    )
    fake_npm.chmod(0o755)

    uv_log = tmp_path / "uv.log"
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        """#!/bin/sh
printf '%s\\n' "$*" > "$UV_LOG"
mkdir -p "$PWD/docs/_site"
printf '<html>docs</html>\\n' > "$PWD/docs/_site/index.html"
""",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)

    subprocess.run(
        ["bash", str(script)],
        cwd=repo,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "UV_LOG": str(uv_log),
        },
        check=True,
        capture_output=True,
        text=True,
    )
    assert uv_log.read_text(encoding="utf-8").strip() == (
        "run --isolated --locked --python 3.12 --with markdown-it-py "
        "--with mdit-py-plugins --with pygments python -m scripts.docs_site.build"
    )
    staged_chat = (
        repo / "apps" / "server" / "openprogram_server" / "_webui" / "_frontend" / "chat.html"
    )
    assert 'id="sidebar"' in staged_chat.read_text(encoding="utf-8")
    assert (repo / "apps/cli/python/openprogram_cli/dist/index.mjs").is_file()

    fake_npm.write_text(
        """#!/bin/sh
if [ "$1" = "run" ] && [ "$2" = "build" ]; then
  mkdir -p "$PWD/apps/web/out"
  printf '<html>web</html>\\n' > "$PWD/apps/web/out/index.html"
  printf '<main aria-label="Authenticating"></main>\\n' > "$PWD/apps/web/out/chat.html"
fi
if [ "$1" = "run" ] && [ "$2" = "build:standalone" ]; then
  mkdir -p "$PWD/apps/cli/dist"
  printf 'standalone\\n' > "$PWD/apps/cli/dist/index-standalone.cjs"
fi
""",
        encoding="utf-8",
    )
    rejected = subprocess.run(
        ["bash", str(script)],
        cwd=repo,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "UV_LOG": str(uv_log),
        },
        check=False,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode != 0
    assert "Authenticating" in rejected.stderr



def test_public_docs_follow_the_release_platform_policy() -> None:
    public_docs = [
        ROOT / "README.md",
        ROOT / "docs" / "README.md",
        ROOT / "docs" / "README.zh.md",
        ROOT / "docs" / "capabilities" / "installing-harnesses.md",
        ROOT / "docs" / "capabilities" / "installing-harnesses.zh.md",
        ROOT / "docs" / "reference" / "cli.md",
        ROOT / "docs" / "reference" / "cli.zh.md",
        ROOT / "docs" / "slides" / "openprogram-intro.html",
        ROOT
        / "docs"
        / "superpowers"
        / "specs"
        / "2026-08-11-framework-adoption-homepage-design.md",
    ]
    forbidden = [
        "Any platform",
        "任意平台",
        "Native macOS / Linux / Windows",
        "Cross-platform (macOS / Linux / Windows)",
        "跨平台（macOS / Linux / Windows）",
        "macOS/Linux/Windows",
    ]
    for path in public_docs:
        contents = path.read_text(encoding="utf-8")
        for phrase in forbidden:
            assert phrase not in contents, f"{path.relative_to(ROOT)}: {phrase}"



def test_linux_install_docs_do_not_claim_a_desktop_artifact() -> None:
    expectations = {
        "docs/install/install.md": "no reduced Linux desktop artifact is published",
        "docs/install/install.zh.md": "不发布精简的 Linux 桌面产物",
    }
    for relative, expected in expectations.items():
        contents = (ROOT / relative).read_text(encoding="utf-8")
        assert "linux-x86_64.AppImage" not in contents
        assert expected in contents



def test_public_install_docs_pin_current_product_version() -> None:
    version = _desktop_package()["version"]
    english = (ROOT / "docs" / "install" / "install.md").read_text(encoding="utf-8")
    chinese = (ROOT / "docs" / "install" / "install.zh.md").read_text(
        encoding="utf-8"
    )
    design = (
        ROOT
        / "docs"
        / "reference"
        / "design"
        / "distribution"
        / "installation-packaging.html"
    ).read_text(encoding="utf-8")
    plan = (
        ROOT
        / "docs"
        / "reference"
        / "design"
        / "distribution"
        / "implementation-plan.md"
    ).read_text(encoding="utf-8")

    assert f"OPENPROGRAM_VERSION={version} sh" in english
    assert f"OPENPROGRAM_VERSION={version} sh" in chinese
    assert f"正式版本</strong>：v{version}" in design
    assert f"### Current v{version} release acceptance" in plan



def test_public_docs_describe_one_release_product_runtime() -> None:
    public_docs = [
        ROOT / "README.md",
        ROOT / "docs" / "README.md",
        ROOT / "docs" / "README.zh.md",
        ROOT / "docs" / "install" / "install.md",
        ROOT / "docs" / "install" / "install.zh.md",
        ROOT / "docs" / "install" / "upgrade.md",
        ROOT / "docs" / "install" / "upgrade.zh.md",
        ROOT / "docs" / "capabilities" / "installing-harnesses.md",
        ROOT / "docs" / "capabilities" / "installing-harnesses.zh.md",
        ROOT / "docs" / "capabilities" / "workflows" / "README.md",
        ROOT / "docs" / "capabilities" / "workflows" / "README.zh.md",
        ROOT / "docs" / "capabilities" / "workflows" / "gui-agent.md",
        ROOT / "docs" / "capabilities" / "workflows" / "gui-agent.zh.md",
        ROOT / "docs" / "capabilities" / "workflows" / "research-agent.md",
        ROOT / "docs" / "capabilities" / "workflows" / "research-agent.zh.md",
        ROOT / "docs" / "capabilities" / "workflows" / "wiki-agent.md",
        ROOT / "docs" / "capabilities" / "workflows" / "wiki-agent.zh.md",
        ROOT / "docs" / "capabilities" / "tools.md",
        ROOT / "docs" / "capabilities" / "tools.zh.md",
        ROOT / "docs" / "capabilities" / "README.md",
        ROOT
        / "docs"
        / "capabilities"
        / "agentic-programming"
        / "embedding-in-your-own-stack.md",
        ROOT
        / "docs"
        / "capabilities"
        / "agentic-programming"
        / "embedding-in-your-own-stack.zh.md",
        ROOT / "docs" / "integrations" / "channels.md",
        ROOT / "docs" / "integrations" / "channels.zh.md",
        ROOT / "docs" / "start" / "GETTING_STARTED.md",
        ROOT / "docs" / "start" / "GETTING_STARTED.zh.md",
        ROOT / "docs" / "start" / "faq.md",
        ROOT / "docs" / "start" / "faq.zh.md",
        ROOT / "docs" / "slides" / "openprogram-intro.html",
        ROOT / "docs" / "reference" / "design" / "feature-matrix.html",
    ]
    forbidden = (
        "openprogram programs install gui",
        "openprogram programs install research",
        "openprogram programs install wiki",
        "Agent programs are not part",
        "agent Program 不属于",
        "notarized DMG",
        "exact wheel",
        "精确 wheel",
        "pip install 'openprogram[search]'",
        "pip install openprogram[channels]",
    )
    combined = "\n".join(path.read_text(encoding="utf-8") for path in public_docs)
    for phrase in forbidden:
        assert phrase not in combined
    assert "same product runtime" in combined
    assert "相同的 product runtime" in combined
    assert "PyTorch, OpenCV, and EasyOCR are not in the product runtime" in combined
    assert "product runtime 明确不含 PyTorch、OpenCV 和 EasyOCR" in combined
    assert "Program packages and their supported runtime assets" in combined
    assert "Program package 与受支持的 runtime 资产" in combined
    for inaccurate_claim in (
        "default OCR/model data",
        "默认 OCR/模型数据",
        "Their Python dependencies, default OCR data",
        "它们的 Python 依赖、默认 OCR 数据",
        "their dependencies, and their default runtime assets",
        "三项第一方 Programs、依赖和默认 runtime 资产",
    ):
        assert inaccurate_claim not in combined



def test_public_product_surfaces_do_not_offer_python_package_install() -> None:
    public_files = [
        ROOT / ".github" / "CONTRIBUTING.md",
        ROOT / "docs" / "_static_root" / "llms.txt",
        ROOT / "docs" / "server" / "troubleshooting.md",
        ROOT / "docs" / "server" / "troubleshooting.zh.md",
        ROOT / "docs" / "integrations" / "openclaw.md",
        ROOT / "docs" / "integrations" / "openclaw.zh.md",
    ]
    internal_python_installers = {
        ROOT / "openprogram" / "cli" / "commands" / "browser.py",
        ROOT / "openprogram" / "cli" / "commands" / "plugins.py",
        ROOT / "openprogram" / "cli" / "commands" / "programs.py",
        ROOT / "openprogram" / "cli" / "commands" / "upgrade.py",
        ROOT / "openprogram" / "cli" / "setup_sections" / "sections.py",
        ROOT / "openprogram" / "programs" / "_programs.py",
        ROOT / "openprogram" / "programs" / "_registry.py",
        ROOT / "openprogram" / "updater" / "detect.py",
    }
    public_files.extend(
        path
        for path in (ROOT / "openprogram").rglob("*.py")
        if path not in internal_python_installers
        and ROOT / "openprogram" / "programs" / "applications" not in path.parents
    )
    forbidden = (
        "pip install",
        "pip3 install",
        "pipx install",
        "uv tool install openprogram",
        "pypi.org/project/openprogram",
    )
    for path in public_files:
        source = path.read_text(encoding="utf-8").lower()
        for phrase in forbidden:
            assert phrase not in source, f"{path.relative_to(ROOT)}: {phrase}"


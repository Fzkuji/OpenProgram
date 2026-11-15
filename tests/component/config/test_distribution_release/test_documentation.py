"""release documentation tests."""
from __future__ import annotations
import os
import re
from openprogram.operations.project_site import INSTALL_SCRIPT_URL
from ._support import POSIX_SHELL_INTEGRATION, ROOT, _desktop_package, subprocess


def test_docs_publish_short_installer_at_the_domain_root() -> None:
    workflow = (ROOT / ".github" / "workflows" / "docs-pages.yml").read_text(
        encoding="utf-8"
    )
    assert "mv _publish/docs/install.sh _publish/install" in workflow



def test_normal_user_docs_use_the_short_release_installer() -> None:
    short_command = f"curl -fsSL {INSTALL_SCRIPT_URL} | sh"
    for relative in (
        "README.md",
        "docs/install/install.html",
        "docs/install/install.zh.html",
        "docs/install/upgrade.html",
        "docs/install/upgrade.zh.html",
        "docs/start/GETTING_STARTED.html",
        "docs/start/GETTING_STARTED.zh.html",
        "website/index.html",
    ):
        contents = (ROOT / relative).read_text(encoding="utf-8")
        assert short_command in contents, relative
        assert "v0.6.1/scripts/install-release.sh" not in contents, relative


    # The task-oriented overview links to the installation guide instead of
    # duplicating commands. Both language entries must reach their own guide.
    for suffix in ("", ".zh"):
        overview = (ROOT / f"docs/README{suffix}.html").read_text(encoding="utf-8")
        assert f'href="install/install{suffix}.html"' in overview



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
  mkdir -p "$PWD/openprogram/interfaces/cli/dist"
  printf '// terminal bundle\\n' > "$PWD/openprogram/interfaces/cli/dist/index.mjs"
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
        repo / "openprogram" / "interfaces" / "server" / "_frontend" / "chat.html"
    )
    assert 'id="sidebar"' in staged_chat.read_text(encoding="utf-8")
    assert (repo / "openprogram/interfaces/cli/dist/index.mjs").is_file()

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
        ROOT / "docs" / "README.html",
        ROOT / "docs" / "README.zh.html",
        ROOT / "docs" / "capabilities" / "installing-programs.html",
        ROOT / "docs" / "capabilities" / "installing-programs.zh.html",
        ROOT / "docs" / "reference" / "cli.html",
        ROOT / "docs" / "reference" / "cli.zh.html",
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



def test_install_docs_always_offer_the_newest_release() -> None:
    """The first download links follow each release instead of naming one.

    The Install page once linked the v1.1.0 DMGs and called v1.1.0 the latest
    release long after newer ones shipped. Its App links now go through
    latest.html, which finds the file in the newest release, and no install
    page names a release to download from.
    """
    latest = (ROOT / "docs/_static_root/latest.js").read_text(encoding="utf-8")
    first_links = ("OpenProgram-mac-arm64.dmg", "OpenProgram-mac-x64.dmg", "OpenProgram-win-x64.exe")
    for relative in ("docs/install/install.html", "docs/install/install.zh.html"):
        contents = (ROOT / relative).read_text(encoding="utf-8")
        choose = contents[contents.index('id="choose"'):contents.index("</table>", contents.index('id="choose"'))]
        for name in first_links:
            assert f'href="../latest.html?{name}"' in choose, f"{relative}: {name}"
            assert f'"{name}"' in latest
        assert 'id="release-support"' in contents, relative
    for relative in ("install.html", "install.zh.html", "download.html", "download.zh.html"):
        contents = (ROOT / "docs/install" / relative).read_text(encoding="utf-8")
        assert re.search(r"releases/download/v\d", contents) is None, relative
        assert re.search(r"\bv\d+\.\d+\.\d+\b", contents) is None, relative


def test_install_docs_cover_what_stops_a_new_user() -> None:
    fallbacks = (
        "https://raw.githubusercontent.com/FuzzyKuji/OpenProgram/main/docs/_static_root/install.sh | sh",
        "https://raw.githubusercontent.com/FuzzyKuji/OpenProgram/main/docs/_static_root/install.ps1 | iex",
    )
    required = (
        "macOS 13",
        "~/Applications",
        "-unsigned",
        "fuse3",
        "APPIMAGE_EXTRACT_AND_RUN=1",
        "HTTPS_PROXY",
        "NO_PROXY",
        "release-manifest.json",
    )
    for relative in ("docs/install/install.html", "docs/install/install.zh.html"):
        contents = (ROOT / relative).read_text(encoding="utf-8")
        for needle in (*fallbacks, *required):
            assert needle in contents, f"{relative}: {needle}"
    assert "Smart App Control" in (ROOT / "docs/install/install.html").read_text(encoding="utf-8")
    assert "智能应用控制" in (ROOT / "docs/install/install.zh.html").read_text(encoding="utf-8")


def test_every_readme_gives_the_working_install_fallback() -> None:
    fallback = "https://raw.githubusercontent.com/FuzzyKuji/OpenProgram/main/docs/_static_root/install.sh"
    readmes = sorted(ROOT.glob("README*.md"))
    assert len(readmes) == 10
    for path in readmes:
        contents = path.read_text(encoding="utf-8")
        assert f"curl -fsSL {INSTALL_SCRIPT_URL} | sh" in contents, path.name
        assert fallback in contents, path.name


def test_download_page_is_registered_and_lists_every_app_file() -> None:
    from scripts.docs_site.nav import PAGE_ORDER, TAB_SECTIONS

    assert "install/download.html" in PAGE_ORDER
    assert any("install/download.html" in paths for _en, _zh, paths in TAB_SECTIONS["install"])
    latest = (ROOT / "docs/_static_root/latest.js").read_text(encoding="utf-8")
    for relative in ("docs/install/download.html", "docs/install/download.zh.html"):
        contents = (ROOT / relative).read_text(encoding="utf-8")
        for name in (
            "OpenProgram-mac-arm64.dmg",
            "OpenProgram-mac-x64.dmg",
            "OpenProgram-linux-amd64.deb",
            "OpenProgram-linux-x86_64.AppImage",
            "OpenProgram-linux-arm64.deb",
            "OpenProgram-linux-arm64.AppImage",
            "OpenProgram-win-x64.exe",
        ):
            # A release has no file under this fixed name; the link goes
            # through latest.html, which finds the versioned file. The link
            # is relative, so it works wherever the site is served.
            assert f'href="../latest.html?{name}"' in contents, f"{relative}: {name}"
            assert f'"{name}"' in latest
        assert "releases/latest/download/" not in contents



def test_public_install_docs_pin_current_product_version() -> None:
    version = _desktop_package()["version"]
    english = (ROOT / "docs" / "install" / "install.html").read_text(encoding="utf-8")
    chinese = (ROOT / "docs" / "install" / "install.zh.html").read_text(
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
        / "implementation-plan.html"
    ).read_text(encoding="utf-8")

    # The docs must not go stale at each release, so the pin example is generic.
    assert "OPENPROGRAM_VERSION=X.Y.Z sh" in english
    assert "OPENPROGRAM_VERSION=X.Y.Z sh" in chinese
    assert f"Official releases</strong>: v{version}" in design
    chinese_design = (
        ROOT / "docs/reference/design/distribution/installation-packaging.zh.html"
    ).read_text(encoding="utf-8")
    assert f"正式版本</strong>：v{version}" in chinese_design
    assert 'id="release-acceptance"' in plan



def test_public_docs_describe_one_release_product_runtime() -> None:
    public_docs = [
        ROOT / "README.md",
        ROOT / "docs" / "README.html",
        ROOT / "docs" / "README.zh.html",
        ROOT / "docs" / "install" / "install.html",
        ROOT / "docs" / "install" / "install.zh.html",
        ROOT / "docs" / "install" / "upgrade.html",
        ROOT / "docs" / "install" / "upgrade.zh.html",
        ROOT / "docs" / "capabilities" / "installing-programs.html",
        ROOT / "docs" / "capabilities" / "installing-programs.zh.html",
        ROOT / "docs" / "capabilities" / "workflows" / "README.html",
        ROOT / "docs" / "capabilities" / "workflows" / "README.zh.html",
        ROOT / "docs" / "capabilities" / "computer-use.html",
        ROOT / "docs" / "capabilities" / "computer-use.zh.html",
        ROOT / "docs" / "capabilities" / "tools.html",
        ROOT / "docs" / "capabilities" / "tools.zh.html",
        ROOT / "docs" / "capabilities" / "README.html",
        ROOT
        / "docs"
        / "capabilities"
        / "agentic-programming"
        / "embedding-in-your-own-stack.html",
        ROOT
        / "docs"
        / "capabilities"
        / "agentic-programming"
        / "embedding-in-your-own-stack.zh.html",
        ROOT / "docs" / "integrations" / "channels.html",
        ROOT / "docs" / "integrations" / "channels.zh.html",
        ROOT / "docs" / "start" / "GETTING_STARTED.html",
        ROOT / "docs" / "start" / "GETTING_STARTED.zh.html",
        ROOT / "docs" / "start" / "faq.html",
        ROOT / "docs" / "start" / "faq.zh.html",
        ROOT / "docs" / "reference" / "design" / "feature-matrix.html",
    ]
    forbidden = (
        "openprogram programs install gui",
        "openprogram programs install research",
        "openprogram programs install wiki",
        "Agent programs are not part",
        "agent Program 不属于",
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
    # A release bundles no Programs; none is described as included.
    assert "Research Program" not in combined
    assert "Research 第一方 Program" not in combined
    assert "It bundles no Programs." in combined
    assert "不捆绑任何 Program" in combined
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
        ROOT / "docs" / "server" / "troubleshooting.html",
        ROOT / "docs" / "server" / "troubleshooting.zh.html",
        ROOT / "docs" / "integrations" / "openclaw.html",
        ROOT / "docs" / "integrations" / "openclaw.zh.html",
    ]
    internal_python_installers = {
        ROOT / "openprogram" / "interfaces" / "cli" / "commands" / "browser.py",
        ROOT / "openprogram" / "interfaces" / "cli" / "commands" / "plugins.py",
        ROOT / "openprogram" / "interfaces" / "cli" / "commands" / "programs.py",
        ROOT / "openprogram" / "interfaces" / "cli" / "commands" / "upgrade.py",
        ROOT / "openprogram" / "interfaces" / "cli" / "setup_sections" / "sections.py",
        ROOT / "openprogram" / "programs" / "_host" / "sources.py",
        ROOT / "openprogram" / "programs" / "_host" / "registry.py",
        ROOT / "openprogram" / "operations" / "self_update" / "github_release" / "detect.py",
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


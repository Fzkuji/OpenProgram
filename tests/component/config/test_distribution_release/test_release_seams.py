"""The release, the installers and the updaters agree on names and formats.

Each side is owned by different code in different languages, so nothing else
notices when one of them renames a file.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import textwrap

import pytest

from openprogram.operations.self_update.github_release import github
from openprogram.operations.self_update.official import assets

ROOT = Path(__file__).resolve().parents[4]
UPDATE_SERVICE = ROOT / "apps/desktop/main/self-update/update-service.js"
VERSION = "9.8.7"

# node platform, node arch, options, then the names in the order they are tried.
DESKTOP_NAMES = [
    ("darwin", "arm64", {}, ["mac-arm64.dmg", "mac-arm64-unsigned.dmg"]),
    ("darwin", "x64", {}, ["mac-x64.dmg", "mac-x64-unsigned.dmg"]),
    ("win32", "x64", {}, ["win-x64.exe"]),
    ("win32", "x64", {"windowsSignedOnly": False}, ["win-x64.exe", "win-x64-unsigned.exe"]),
    ("win32", "arm64", {"windowsSignedOnly": False}, ["win-arm64.exe", "win-arm64-unsigned.exe"]),
    ("linux", "x64", {"linuxFormat": "appimage"}, ["linux-x86_64.AppImage"]),
    ("linux", "arm64", {"linuxFormat": "appimage"}, ["linux-arm64.AppImage"]),
    ("linux", "x64", {"linuxFormat": "deb"}, ["linux-amd64.deb"]),
    ("linux", "arm64", {"linuxFormat": "deb"}, ["linux-arm64.deb"]),
]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _node(script: str) -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    completed = subprocess.run([node, "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def _release_matrix():
    spec = importlib.util.spec_from_file_location("release_matrix_seams", ROOT / "scripts/release/release-matrix.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _builder_name(os_name: str, node_arch: str, extension: str) -> str:
    """What electron-builder's artifactName and arch rules give for this target."""
    pattern = json.loads(_read("apps/desktop/package.json"))["build"]["artifactName"]
    arch = node_arch
    if node_arch == "x64" and extension == "AppImage":
        arch = "x86_64"
    if node_arch == "x64" and extension == "deb":
        arch = "amd64"
    return (pattern.replace("${productName}", "OpenProgram").replace("${version}", VERSION)
            .replace("${os}", os_name).replace("${arch}", arch).replace("${ext}", extension))


def _latest_link(fixed: str, release_files: list[str]) -> str:
    """Where docs/_static_root/latest.js sends ``latest.html?<fixed>``."""
    release = {"assets": [
        {"name": name, "browser_download_url":
            f"https://github.com/FuzzyKuji/OpenProgram/releases/download/v{VERSION}/{name}"}
        for name in release_files
    ]}
    script = (
        f"globalThis.location = {{search: {json.dumps('?' + fixed)}, hash: '',"
        " replace: (url) => console.log(url)};"
        f"globalThis.fetch = async () => ({{ok: true, json: async () => ({json.dumps(release)})}});"
        f"eval(require('fs').readFileSync({json.dumps(str(ROOT / 'docs/_static_root/latest.js'))}, 'utf8'));"
    )
    return _node(script).strip()


def _updater_names(platform: str, arch: str, options: dict) -> list[str]:
    script = (
        f"const s = require({json.dumps(str(UPDATE_SERVICE))});"
        f"console.log(JSON.stringify(s.desktopAssetNames({json.dumps(VERSION)}, {json.dumps(platform)}, "
        f"{json.dumps(arch)}, {json.dumps(options)})));"
    )
    return json.loads(_node(script))


@pytest.mark.parametrize(("platform", "arch", "options", "suffixes"), DESKTOP_NAMES)
def test_app_updater_looks_for_the_files_the_release_builds(platform, arch, options, suffixes, tmp_path):
    expected = [f"OpenProgram-{VERSION}-{suffix}" for suffix in suffixes]
    assert _updater_names(platform, arch, options) == expected

    os_name = {"darwin": "mac", "win32": "win", "linux": "linux"}[platform]
    built = _builder_name(os_name, arch, expected[0].rsplit(".", 1)[1])
    assert built == expected[0]

    # Each is uploaded once; the docs site's newest-download link finds it under
    # one fixed name, signed or not.
    fixed = f"OpenProgram-{os_name}-{re.sub(r'.*-(?:mac|win|linux)-', '', expected[0]).replace('-unsigned', '')}"
    assert json.dumps(fixed) in _read("docs/_static_root/latest.js")
    for name in expected:
        assert _latest_link(fixed, [f"OpenProgram-{VERSION}-runtime-linux-x86_64.tar.gz", name]) == (
            f"https://github.com/FuzzyKuji/OpenProgram/releases/download/v{VERSION}/{name}"
        )


def test_newest_download_link_opens_the_release_page_when_it_cannot_find_the_file():
    page = "https://github.com/FuzzyKuji/OpenProgram/releases/latest"
    dmg = f"OpenProgram-{VERSION}-mac-arm64.dmg"
    # A name that is not ours, and a release without that file.
    assert _latest_link("OpenProgram-mac-arm64.zip", [f"OpenProgram-{VERSION}-mac-arm64.zip"]) == page
    assert _latest_link("OpenProgram-mac-x64.dmg", [dmg]) == page
    # No answer from the API.
    script = (
        "globalThis.location = {search: '?OpenProgram-mac-arm64.dmg', hash: '', replace: (url) => console.log(url)};"
        "globalThis.fetch = async () => ({ok: false, status: 403});"
        f"eval(require('fs').readFileSync({json.dumps(str(ROOT / 'docs/_static_root/latest.js'))}, 'utf8'));"
    )
    assert _node(script).strip() == page
    # A download URL on another host is never followed.
    script = script.replace(
        "({ok: false, status: 403})",
        "({ok: true, json: async () => ({assets: [{name: " + json.dumps(dmg)
        + ", browser_download_url: 'https://example.com/" + dmg + "'}]})})",
    )
    assert _node(script).strip() == page


def test_unsigned_names_come_from_the_release_steps():
    workflow = _read(".github/workflows/release.yml")
    assert 'Rename-Item -LiteralPath $_.FullName -NewName ($_.BaseName + "-unsigned.exe")' in workflow
    assert "name = f'OpenProgram-{version}-mac-{options.arch}-unsigned'" in _read("scripts/release/ci-sign-macos.py")
    assert "name = f'OpenProgram-{version}-mac-{args.arch}'" in _read("scripts/release/sign-macos-release.py")
    assert "--arch ${{ matrix.builder_arch }}" in workflow


@pytest.mark.parametrize(
    ("path", "spellings"),
    [
        ("scripts/release/install-release.sh", [
            "OpenProgram-${OPENPROGRAM_VERSION}-runtime-${platform}-${arch}.tar.gz",
            "OpenProgram-${OPENPROGRAM_VERSION}-seed-${platform}-${arch}.tar.gz",
        ]),
        ("scripts/release/install-release.ps1", [
            "OpenProgram-$Version-runtime-windows-$Arch.zip",
            "OpenProgram-$Version-seed-windows-$Arch.tar.gz",
        ]),
        ("docs/_static_root/install.sh", [
            "OpenProgram-$version-runtime-$platform-$arch.tar.gz",
            "OpenProgram-$version-seed-$platform-$arch.tar.gz",
        ]),
        ("docs/_static_root/install.ps1", [
            "OpenProgram-$Version-runtime-windows-$Arch.zip",
            "OpenProgram-$Version-seed-windows-$Arch.tar.gz",
        ]),
        ("scripts/release/archive-product-runtime.ps1", ["OpenProgram-$Version-runtime-$Platform-$Arch.zip"]),
        (".github/workflows/release.yml", [
            'name="OpenProgram-${RELEASE_TAG#v}-seed-${{ matrix.platform }}-${{ matrix.arch }}.tar.gz"',
        ]),
        ("openprogram/interfaces/cli/commands/upgrade.py", [
            'f"OpenProgram-{version}-runtime-{platform_name}-{arch}{suffix}"',
        ]),
    ],
)
def test_command_line_asset_names_are_spelled_the_same_everywhere(path, spellings):
    source = _read(path)
    for spelling in spellings:
        assert spelling in source


def test_update_platforms_use_the_release_matrix_words():
    matrix = _release_matrix().release_matrices()
    for row in matrix["runtime"]:
        assert assets._PLATFORM.fullmatch(f"{row['platform']}-{row['arch']}")


def test_both_updaters_trust_the_same_release_keys():
    match = re.search(r"const RELEASE_PUBLIC_KEYS = \[(.*?)\];", UPDATE_SERVICE.read_text(encoding="utf-8"), re.S)
    assert match is not None
    in_app = re.findall(r'"([0-9a-f]{64})"', match.group(1))
    assert in_app == list(assets.RELEASE_PUBLIC_KEYS)


def test_installers_accept_the_fixed_hosts_the_fetcher_accepts():
    fixed = github._RELEASE_HOSTS - {"raw.githubusercontent.com"}
    shell = _read("scripts/release/install-release.sh")
    windows = _read("scripts/release/install-release.ps1")
    for host in fixed:
        assert f"https://{host}/*" in shell
        assert f'"{host}"' in windows


def test_app_accepts_an_update_source_signed_by_the_release_tool(tmp_path):
    private_key = tmp_path / "update-key.pem"
    public_key = assets.generate_signing_key(private_key)
    source = tmp_path / "update-source.json"
    source.write_text(json.dumps({"schema": 1, "repository": "New/Home", "hosts": ["files.example.org"]}))
    signature = assets.sign_file(source, private_key.read_bytes())

    assets.verify_signature(source.read_bytes(), signature.read_text(encoding="ascii"), [public_key])
    script = textwrap.dedent(f"""
        const fs = require("node:fs");
        const service = require({json.dumps(str(UPDATE_SERVICE))});
        const files = {{
          "update-source.json": fs.readFileSync({json.dumps(str(source))}),
          "update-source.json.sig": fs.readFileSync({json.dumps(str(signature))}),
        }};
        const fetchFile = async (url) => new Response(files[url.split("/").at(-1)]);
        service.readUpdateSource(fetchFile, [{json.dumps(public_key)}]).then((found) => {{
          console.log(JSON.stringify(found && {{ repository: found.repository, webRoot: found.webRoot }}));
        }});
    """)
    assert json.loads(_node(script)) == {"repository": "New/Home", "webRoot": "https://files.example.org"}


def test_command_line_launchers_put_the_bundled_git_first():
    shell = _read("scripts/release/install-release.sh")
    assert 'export PATH="$runtime_root/current/git/bin:$PATH"' in shell
    assert 'export GIT_EXEC_PATH="$runtime_root/current/git/libexec/git-core"' in shell
    assert 'export GIT_TEMPLATE_DIR="$runtime_root/current/git/share/git-core/templates"' in shell
    assert 'set `"PATH=$CmdGit;%PATH%`"' in _read("scripts/release/install-release.ps1")


def test_deb_declares_what_the_bundled_git_links_against():
    depends = json.loads(_read("apps/desktop/package.json"))["build"]["deb"]["depends"]
    assert "libcurl4 | libcurl4t64" in depends
    assert "zlib1g" in depends


def test_command_prompt_installer_is_served_from_the_site_root():
    assert "mv _publish/docs/install.cmd _publish/install.cmd" in _read(".github/workflows/docs-pages.yml")

"""release staging tests."""
from __future__ import annotations
from ._support import (
    ROOT,
    subprocess,
)


def test_release_frontend_staging_removes_stale_export_before_build() -> None:
    staging = (ROOT / "scripts" / "release" / "stage-release-assets.sh").read_text(
        encoding="utf-8"
    )
    cleanup = staging.index('rm -rf "$source_dir"')
    build = staging.index("npm run build --workspace apps/web")
    assert cleanup < build


def test_only_a_checkout_that_asks_keeps_its_bundler_cache_and_packages() -> None:
    root = ROOT / "scripts"
    staging = (root / "release" / "stage-release-assets.sh").read_text(encoding="utf-8")
    assert 'incremental="${OPENPROGRAM_INCREMENTAL_ASSETS:-}"' in staging
    kept = staging.index('test -n "$incremental" && test -d "$next_build_dir/cache"')
    assert kept < staging.index('rm -rf "$next_build_dir"') < staging.index("npm run build --workspace apps/web")
    assert staging.index('test -n "$incremental" && test -d node_modules') < staging.index("npm ci --ignore-scripts")
    users = [path.relative_to(ROOT).as_posix() for path in sorted(root.rglob("*"))
             if path.is_file() and path.suffix in {".sh", ".py", ".yml"}
             and "OPENPROGRAM_INCREMENTAL_ASSETS=1" in path.read_text(encoding="utf-8", errors="ignore")
             and path.name != "stage-release-assets.sh"]
    assert users == ["scripts/local-instances/refresh-dev-instance.sh"], "a release build starts from nothing"
    workflows = "".join(path.read_text(encoding="utf-8") for path in (ROOT / ".github" / "workflows").glob("*.yml"))
    assert "OPENPROGRAM_INCREMENTAL_ASSETS" not in workflows


def test_an_unchanged_export_is_staged_again_only_where_the_checkout_asks() -> None:
    staging = (ROOT / "scripts" / "release" / "stage-release-assets.sh").read_text(encoding="utf-8")
    asked = staging.index('if test -n "$incremental"; then\n    web_stamp="$(web_inputs_stamp)"')
    reused = staging.index('test "$web_stamp" = "$(cat "$web_stamp_file" 2>/dev/null)"')
    forgotten = staging.index('rm -f "$web_stamp_file"')
    built = staging.index("npm run build --workspace apps/web")
    recorded = staging.index('printf \'%s\\n\' "$web_stamp" > "$web_stamp_file"')
    assert asked < reused < forgotten < built < recorded, "a build that fails leaves no record of its inputs"
    # A checkout with changes Git does not hold has no stamp, so it is built.
    assert 'git status --porcelain --untracked-files=all 2>/dev/null || echo unknown' in staging

    installer = (ROOT / "scripts" / "local-instances" / "install-dev-app.sh").read_text(encoding="utf-8")
    kept = installer.index('test "$shell_inputs" = "$(cat "$shell_cache/inputs" 2>/dev/null)"')
    build = installer.index('"$repo_root/scripts/release/build-desktop-shell.sh" "$work_dir/shell"')
    named = installer.index('plutil -replace CFBundleIdentifier')
    assert kept < build < named, "the kept shell is the one the build left, before it is named and signed"
    for path in ("apps/desktop", "package-lock.json", "scripts/release/build-desktop-shell.sh"):
        assert path in installer[installer.index("shell_inputs="):kept]


def test_release_staging_builds_self_contained_tui() -> None:
    staging = (ROOT / "scripts" / "release" / "stage-release-assets.sh").read_text(
        encoding="utf-8"
    )
    builder = (ROOT / "scripts" / "release" / "build-product-runtime.sh").read_text(
        encoding="utf-8"
    )
    assert "npm run build:standalone --workspace apps/cli" in staging
    assert 'cp "$(command -v node)" "$runtime_root/bin/node"' in builder
    assert "assets/tui/index.cjs" in builder



def test_release_frontend_staging_replaces_the_previous_assets() -> None:
    staging = (ROOT / "scripts" / "release" / "stage-release-assets.sh").read_text(
        encoding="utf-8"
    )
    assert 'rm -rf "$target_dir"' in staging



def test_release_frontend_staging_directory_is_ignored() -> None:
    generated = "openprogram/interfaces/server/_frontend/index.html"
    result = subprocess.run(
        ["git", "check-ignore", "-q", generated],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr



def test_posix_runtime_archive_is_labeled_and_published_atomically() -> None:
    archiver = (ROOT / "scripts" / "release" / "archive-product-runtime.sh").read_text(
        encoding="utf-8"
    )
    assert 'test "$manifest_platform" = "$platform"' in archiver
    assert 'test "$manifest_arch" = "$arch"' in archiver
    assert 'archive_tmp="$(mktemp "$output_dir/.openprogram-runtime.XXXXXX")"' in archiver
    assert 'chmod 0644 "$archive_tmp" "$checksum_tmp"' in archiver
    assert 'mv -f "$archive_tmp" "$archive"' in archiver
    assert "$(basename \"$archive\")" in archiver


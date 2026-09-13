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



def test_release_frontend_staging_removes_legacy_package_assets() -> None:
    staging = (ROOT / "scripts" / "release" / "stage-release-assets.sh").read_text(
        encoding="utf-8"
    )
    assert 'legacy_target_dir="$repo_root/openprogram/webui/_frontend"' in staging
    assert 'rm -rf "$target_dir" "$legacy_target_dir"' in staging



def test_release_frontend_staging_directory_is_ignored() -> None:
    generated = "apps/server/openprogram_server/_webui/_frontend/index.html"
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


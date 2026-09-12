"""Strict, portable validation for the generated OnlyOffice font pack."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

FONT_EXTENSIONS = {".ttf", ".tte", ".otf", ".otc", ".ttc", ".woff", ".woff2"}
EXPECTED_FONT_FILES = 126
EXPECTED_THUMBNAILS = 10
EXPECTED_ALLFONTS_SHA256 = "4727768a8d3bbaa411d53344b13fea658c3f2dac71c13fae2261411fa8c5f67b"
EXPECTED_SELECTION_SHA256 = "508bf86f3f59358dd82f9d8aac79c2fa1276e6444a49cecb91ec9bca6bd6552b"
_ARRAY = re.compile(r'window\["(?P<name>[^"\n]+)"\]\s*=\s*(?P<value>\[[\s\S]*?\]);')


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"invalid object: {path.name}")
    return value


def _array(source: str, name: str) -> list[Any]:
    for match in _ARRAY.finditer(source):
        if match.group("name") == name:
            try:
                value = json.loads(match.group("value"))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid AllFonts.js array: {name}") from exc
            if not isinstance(value, list):
                raise ValueError(f"invalid AllFonts.js array: {name}")
            return value
    raise ValueError(f"missing AllFonts.js array: {name}")


def _safe_relative(root: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"invalid {label} path")
    candidate = (root / value).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} escapes pack: {value}") from exc
    if not candidate.is_file():
        raise ValueError(f"missing {label}: {value}")
    return candidate


def _input_fonts(input_root: Path) -> list[Path]:
    return sorted(
        p for p in input_root.rglob("*") if p.is_file() and p.suffix.lower() in FONT_EXTENSIONS
    )


def _license_files(input_root: Path) -> list[Path]:
    return sorted(
        p
        for p in input_root.rglob("*")
        if p.is_file()
        and (p.name.lower().startswith(("license", "ofl")) or p.name.lower().endswith(".license"))
    )


def _manifest_digest(manifest: dict[str, Any], digests: dict[str, str]) -> str:
    descriptor = {
        "allFonts": manifest["allFonts"],
        "allFontsSha256": digests[manifest["allFonts"]],
        "fontSelection": manifest["fontSelection"],
        "fontSelectionSha256": digests[manifest["fontSelection"]],
        "fontSourceMap": manifest.get("fontSourceMap"),
        "fontSourceMapSha256": digests[manifest["fontSourceMap"]],
        "fontThumbnails": [{"path": p, "sha256": digests[p]} for p in manifest["fontThumbnails"]],
        "fonts": [{"path": p, "sha256": digests[p]} for p in manifest["fonts"]],
    }
    encoded = json.dumps(descriptor, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def validate_font_assets(pack: Path, input: Path) -> dict[str, Any]:
    """Validate a generated pack against its supplied font input directory.

    The returned provenance contains only paths relative to ``input``.  Source
    map paths are treated as labels; identity is established by basename and
    SHA-256, so staging-directory paths cannot escape into the published pack.
    """
    pack = Path(pack).resolve()
    input_root = Path(input).resolve()
    if not pack.is_dir() or not input_root.is_dir():
        raise ValueError("font pack and input must be directories")
    manifest = _read_json(pack / "onlyoffice-browser-font-assets.json")
    if manifest.get("version") != 1:
        raise ValueError("unsupported font asset manifest version")
    for key in ("allFonts", "fontSelection", "fontThumbnails", "fonts"):
        if key not in manifest:
            raise ValueError(f"font manifest is missing {key}")
    if manifest.get("fontSourceMap") != "onlyoffice-browser-font-source-map.json":
        raise ValueError("font manifest has non-canonical source map")
    fonts = manifest["fonts"]
    thumbnails = manifest["fontThumbnails"]
    if not isinstance(fonts, list) or len(fonts) != EXPECTED_FONT_FILES or len(set(fonts)) != len(fonts):
        raise ValueError(f"font manifest must contain exactly {EXPECTED_FONT_FILES} unique font files")
    if not isinstance(thumbnails, list) or len(thumbnails) != EXPECTED_THUMBNAILS or len(set(thumbnails)) != len(thumbnails):
        raise ValueError(f"font manifest must contain exactly {EXPECTED_THUMBNAILS} unique thumbnails")

    paths: dict[str, Path] = {}
    for label in ("allFonts", "fontSelection", "fontSourceMap"):
        paths[manifest[label]] = _safe_relative(pack, manifest[label], label)
    for value in [*fonts, *thumbnails]:
        paths[value] = _safe_relative(pack, value, "font asset")
    if not all(str(p).startswith("fonts/") for p in fonts):
        raise ValueError("font manifest contains a non-font path")

    all_fonts = _array(paths[manifest["allFonts"]].read_text(encoding="utf-8"), "__fonts_files")
    _array(paths[manifest["allFonts"]].read_text(encoding="utf-8"), "__fonts_infos")
    _array(paths[manifest["allFonts"]].read_text(encoding="utf-8"), "__fonts_ranges")
    if len(all_fonts) != EXPECTED_FONT_FILES or sorted(f"fonts/{x.lstrip('/')}" for x in all_fonts) != sorted(fonts):
        raise ValueError("AllFonts.js does not enumerate the manifest's font files")

    source_map = _read_json(paths[manifest["fontSourceMap"]])
    entries = source_map.get("fonts")
    if not isinstance(entries, list) or len(entries) != EXPECTED_FONT_FILES:
        raise ValueError("source map must contain one entry per packed font")
    by_name_hash = {(_p.name.casefold(), _sha256(_p)): _p for _p in _input_fonts(input_root)}
    provenance = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("file"), str) or not isinstance(entry.get("source"), str):
            raise ValueError("source map has an invalid entry")
        packed = entry["file"]
        source = entry["source"]
        if packed in seen or packed not in paths or not packed.startswith("fonts/"):
            raise ValueError(f"source map references an unexpected font: {packed}")
        if re.match(r"^(?:[a-z][a-z\d+.-]*:)?//|^(?:data|blob|file):", source, re.I) or re.search(r"(?:^|[/\\])(usr|System|Library|tmp)(?:[/\\]|$)", source, re.I):
            raise ValueError(f"source map contains a system or external source: {source}")
        packed_path = paths[packed]
        match = by_name_hash.get((Path(source.replace("\\", "/")).name.casefold(), _sha256(packed_path)))
        if match is None:
            raise ValueError(f"source map font does not match an input SHA-256: {source} -> {packed}")
        seen.add(packed)
        provenance.append({"packed": packed, "source": match.relative_to(input_root).as_posix(), "sha256": _sha256(packed_path)})
    if seen != set(fonts):
        raise ValueError("source map does not cover every manifest font")

    digests = {name: _sha256(path) for name, path in paths.items()}
    if digests[manifest["allFonts"]] != EXPECTED_ALLFONTS_SHA256:
        raise ValueError("AllFonts.js digest does not match the reviewed production asset")
    if digests[manifest["fontSelection"]] != EXPECTED_SELECTION_SHA256:
        raise ValueError("font_selection.bin digest does not match the reviewed production asset")
    digest = _manifest_digest(manifest, digests)
    declared = manifest.get("manifestDigest")
    if declared is not None and declared != digest:
        raise ValueError("font manifest digest mismatch")
    licenses = _license_files(input_root)
    if not licenses:
        raise ValueError("font input has no license files")
    license_dirs = {license_path.parent for license_path in licenses}
    uncovered = []
    for font_path in _input_fonts(input_root):
        directory = font_path.parent
        if not any(parent in license_dirs for parent in (directory, *directory.parents)):
            uncovered.append(font_path.relative_to(input_root).as_posix())
    if uncovered:
        raise ValueError(f"input fonts lack an ancestor license: {uncovered[0]}")
    return {
        "fontSet": manifest.get("fontSet", "unknown"),
        "manifestDigest": digest,
        "fonts": EXPECTED_FONT_FILES,
        "thumbnails": EXPECTED_THUMBNAILS,
        "selection": manifest["fontSelection"],
        "allFonts": manifest["allFonts"],
        "provenance": provenance,
        "licenses": [p.relative_to(input_root).as_posix() for p in licenses],
    }

"""Check that default (English) doc sources contain no Chinese text.

Run:  python -m tools.docs_site.checklang
Scans every product-area ``xxx.md`` (not ``xxx.zh.md``) for CJK characters.
The design-notes archive (reference/design/) is exempt — its zh-only notes
stay Chinese by design.
"""
from __future__ import annotations

import re
from pathlib import Path

DOCS = Path(__file__).resolve().parents[2] / "docs"
_CJK = re.compile(r"[一-鿿]")
_EXEMPT = ("reference/design/",)


def main() -> int:
    bad: list[tuple[str, int, str]] = []
    for p in DOCS.rglob("*.md"):
        rel = str(p.relative_to(DOCS)).replace("\\", "/")
        if p.name.endswith(".zh.md") or rel.startswith(("_site", "images", "slides")):
            continue
        if any(rel.startswith(e) for e in _EXEMPT):
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if _CJK.search(line):
                if "中文</a>" in line:  # language-switcher label, written in its target language
                    continue
                bad.append((rel, i, line.strip()[:80]))
    for rel, i, line in bad:
        print(f"{rel}:{i}: {line}")
    print(f"{len(bad)} Chinese line(s) in default-English pages")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())

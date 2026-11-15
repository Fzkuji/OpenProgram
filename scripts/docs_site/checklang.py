"""Check default English prose, including design notes, HTML documents, and SVG diagrams.

Run: python -m scripts.docs_site.checklang
Code examples, scripts, styles and compatibility-anchor attributes are not prose.
Chinese counterparts use the .zh suffix and are checked through bilingual reviews.
scripts.docs_site.check reuses prose_violations; this command stays for the whole tree.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

from .nav import is_excluded

DOCS = Path(__file__).resolve().parents[2] / "docs"
_CJK = re.compile(r"[一-鿿]")


class _ProseParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip: list[str] = []
        self.bad: list[tuple[int, str]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        for name, value in attrs:
            if name in {"alt", "title", "aria-label", "placeholder"} and value:
                if _CJK.search(value) and value.strip() not in {"中文", "简体中文"}:
                    self.bad.append((self.getpos()[0], f"{name}: {value[:100]}"))
        fields = dict(attrs)
        if tag == "meta" and (fields.get("name") or "").lower() == "description" and _CJK.search(fields.get("content") or ""):
            self.bad.append((self.getpos()[0], f"description: {fields['content'][:100]}"))
        if tag in {"script", "style", "pre", "code"}:
            self.skip.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if self.skip and tag == self.skip[-1]:
            self.skip.pop()

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        start = self.getpos()[0]
        for offset, line in enumerate(data.splitlines()):
            if _CJK.search(line) and line.strip() not in {"中文", "简体中文"}:
                self.bad.append((start + offset, line.strip()[:100]))


def prose_violations(source: str, *, markdown: bool) -> list[tuple[int, str]]:
    if markdown:
        # Preserve source line numbers while removing fenced and inline code.
        def blank(match):
            return "\n" * match.group(0).count("\n")

        source = re.sub(r"^\s*(`{3,}|~{3,})[^\n]*\n.*?^\s*\1\s*$", blank, source, flags=re.M | re.S)
        source = re.sub(r"(`+)[^\n]*?\1", blank, source)
    parser = _ProseParser()
    parser.feed(source)
    return parser.bad


def main() -> int:
    bad: list[tuple[str, int, str]] = []
    for p in sorted(DOCS.rglob("*")):
        if p.suffix not in {".md", ".html", ".svg"}:
            continue
        rel = p.relative_to(DOCS)
        if p.name.endswith((".zh.md", ".zh.html", ".zh.svg")) or is_excluded(rel):
            continue
        bad.extend((rel.as_posix(), line, text) for line, text in prose_violations(
            p.read_text(encoding="utf-8", errors="replace"), markdown=p.suffix == ".md"
        ))
    for rel, line, text in bad:
        print(f"{rel}:{line}: {text}")
    print(f"{len(bad)} Chinese prose line(s) in default-English pages")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())

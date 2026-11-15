"""Rules a page's shape must meet. The build applies these too, so a bad page cannot ship."""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Iterator
from pathlib import Path

from .. import checklang, html_source, nav
from . import corpus as loader
from . import policy
from .model import ERROR, WARNING, Corpus, Finding, Page, Scope

_OUTLINE = {"h1", "h2", "h3", "h4", "h5", "h6", "table", "tr", "li", "pre", "details", "figure"}
_EMBEDS = {"iframe", "object", "embed"}
# Findings that make a page unsafe to show, so a quarantine build replaces it.
UNSAFE_TO_RENDER = frozenset({
    "format.style-element", "format.script-element", "format.stylesheet-link", "format.style-attribute",
    "format.event-handler", "format.javascript-url", "format.embed",
})


class DocsFormatError(Exception):
    """One or more pages break the format; the build refuses to publish them."""

    def __init__(self, findings: list[Finding]) -> None:
        self.findings = findings
        lines = [finding.render() for finding in findings]
        super().__init__(f"{len(findings)} docs format error(s):\n" + "\n".join(lines))


def _finding(rule: str, path: str, line: int, message: str, fix: str, subject: str = "") -> Finding:
    return Finding(rule, ERROR, path, line, message, fix, subject)


def _markup(path: str, tree: html_source.Node) -> Iterator[Finding]:
    for node in html_source.walk(tree):
        if node.tag == "style":
            yield _finding("format.style-element", path, node.line, "a <style> element.",
                           "move the rule to scripts/docs_site/assets/site.css, or delete it.")
        elif node.tag == "script":
            yield _finding("format.script-element", path, node.line, "a <script> element.",
                           "delete it; only a page in PROTOTYPE_PAGES may run a script.")
        elif node.tag == "link" and "stylesheet" in node.attrs.get("rel", "").lower():
            yield _finding("format.stylesheet-link", path, node.line, "a stylesheet link.",
                           "use a site class.")
        elif node.tag in _EMBEDS:
            yield _finding("format.embed", path, node.line, f"an <{node.tag}> element embeds other code.",
                           "link to it or use a figure; only a page in PROTOTYPE_PAGES may embed.")
        for name, value in node.attrs.items():
            if name.startswith("on"):
                yield _finding("format.event-handler", path, node.line, f"an inline {name} handler.",
                               "delete it; only a page in PROTOTYPE_PAGES may run a script.", name)
            elif name in {"href", "src", "xlink:href"} and value.strip().lower().startswith("javascript:"):
                yield _finding("format.javascript-url", path, node.line, f"a javascript: URL in {name}.",
                               "link to a real address.", value)
        if "style" in node.attrs:
            yield _finding("format.style-attribute", path, node.line, "a style attribute.",
                           "use a site class.")


def _required(path: str, tree: html_source.Node, needs_heading: bool) -> Iterator[Finding]:
    nodes = list(html_source.walk(tree))
    if not next((n for n in nodes if n.tag == "title" and html_source.text_of(n).strip()), None):
        yield _finding("format.missing-title", path, 1, "no <title>.", "add <title>Page name</title> first in the file.")
    described = any(n.tag == "meta" and n.attrs.get("name", "").lower() == "description"
                    and n.attrs.get("content", "").strip() for n in nodes)
    if not described:
        yield _finding("format.missing-description", path, 1, "no meta description.",
                       'add <meta name="description" content="..."> after the title.')
    headings = [n for n in nodes if n.tag == "h1"]
    if not headings and needs_heading:
        yield _finding("format.missing-h1", path, 1, "no <h1>.", "add one <h1> as the page heading.")
    for extra in headings[1:]:
        yield _finding("format.multiple-h1", path, extra.line, "more than one <h1>.",
                       "make this heading an <h2>.")


def outline(tree: html_source.Node) -> list[html_source.Node]:
    """The structural nodes both languages must share, in reading order."""
    return [node for node in html_source.walk(tree) if node.tag in _OUTLINE]


def _structure(page: Page) -> Iterator[Finding]:
    english, chinese = outline(page.tree), outline(page.zh_tree)
    for index in range(max(len(english), len(chinese))):
        en = english[index] if index < len(english) else None
        zh = chinese[index] if index < len(chinese) else None
        if en is not None and zh is not None and en.tag == zh.tag:
            continue
        en_says = f"<{en.tag}> at line {en.line}" if en else "nothing more"
        zh_says = f"<{zh.tag}> at line {zh.line}" if zh else "nothing more"
        yield _finding("format.structure-mismatch", page.path, en.line if en else 0,
                       f"English has {en_says}, but {page.zh_path} has {zh_says}.",
                       "give both languages the same headings, tables, rows, list items, code blocks, folds and figures.")
        return


def _page(page: Page, judged=nav) -> Iterator[Finding]:
    if page.prototype:
        if not any(n.tag == "script" for n in html_source.walk(page.tree)):
            yield _finding("format.prototype-stale", page.path, 0, "listed as a prototype but runs no script.",
                           "remove it from PROTOTYPE_PAGES in scripts/docs_site/governance/policy.py.")
    else:
        yield from _markup(page.path, page.tree)
        if page.zh_tree is not None:
            yield from _markup(page.zh_path, page.zh_tree)
    # A prototype fills its window with the interaction; a heading has no place in it.
    yield from _required(page.path, page.tree, not page.prototype)
    if page.zh_tree is None:
        yield _finding("format.missing-pair", page.path, 0, "no Chinese version.",
                       f"add {page.path[:-len('.html')]}.zh.html with the same structure.")
    else:
        yield from _required(page.zh_path, page.zh_tree, not page.prototype)
        yield from _structure(page)
        yield from _code_spans_differ(page)
    if not _is_registered(page, judged):
        yield _unregistered(page)
    for line, text in checklang.prose_violations(page.text, markdown=False):
        yield _finding("format.cjk-in-english", page.path, line, f"Chinese text in the English page: {text[:60]}.",
                       "move it to the .zh.html.", text)


def _unregistered(page: Page) -> Finding:
    return _finding("format.unregistered", page.path, 0, "not placed in the site navigation.",
                    f"add {page.key} to a section of TAB_SECTIONS in scripts/docs_site/nav.py.")


def _is_registered(page: Page, judged=nav) -> bool:
    if "/" not in page.key:
        return page.key in judged.ROOT_PAGE_TAB
    return any(page.key in paths for sections in judged.TAB_SECTIONS.values() for _en, _zh, paths in sections)


_ANGLE = re.compile(r"<[^<>]*>")
_CJK = re.compile(r"[\u3000-\u30ff\u3400-\u9fff\uff00-\uffef]")


def code_spans(tree: html_source.Node) -> Counter[str]:
    """Each ``translate="no"`` code span; a named placeholder may be translated, so its name is dropped."""
    return Counter(_ANGLE.sub("<>", html_source.text_of(node).strip()) for node in html_source.walk(tree)
                   if node.tag == "code" and node.attrs.get("translate") == "no")


def _span_differences(page: Page) -> tuple[list[str], list[str]]:
    """Spans only English has and only Chinese has, after pairing off those that translate a word inside."""
    english, chinese = code_spans(page.tree), code_spans(page.zh_tree)
    only_english = list((english - chinese).elements())
    only_chinese: list[str] = []
    for span in (chinese - english).elements():
        pair = next((other for other in only_english if _CJK.search(span) and other[:1] == span[:1]), None)
        if pair is None:
            only_chinese.append(span)
        else:
            only_english.remove(pair)
    return only_english, only_chinese


def _code_spans_differ(page: Page) -> Iterator[Finding]:
    if page.prototype or page.zh_tree is None or page.rel in policy.CODE_SPANS_DIFFER:
        return
    only_english, only_chinese = _span_differences(page)
    if only_english or only_chinese:
        said = f"only English has `{only_english[0]}`" if only_english else f"only Chinese has `{only_chinese[0]}`"
        yield _finding("format.code-span-mismatch", page.zh_path, 0,
                       f"code spans differ from {page.path}: {said} ({len(only_english) + len(only_chinese)} in all).",
                       "code is not translated: make both pages name the same thing.", said)


def _stale_exemptions(corpus: Corpus) -> Iterator[Finding]:
    pages = {page.rel: page for page in corpus.pages}
    for rel in sorted(policy.CODE_SPANS_DIFFER):
        page = pages.get(rel)
        if page is not None and page.zh_tree is not None and not any(_span_differences(page)):
            yield _finding("format.code-span-exemption-stale", "scripts/docs_site/governance/policy.py", 0,
                           f"CODE_SPANS_DIFFER lists {rel}, whose code spans match.", "remove it from the list.", rel)


def _chinese_lags(corpus: Corpus, pages: list[Page]) -> Iterator[Finding]:
    """English reading differently while its Chinese page stayed untouched."""
    if corpus.scope.mode == "all":
        return
    changed = set(corpus.scope.changed)
    edited = [page for page in pages if page.path in changed and page.zh_tree is not None and page.zh_path not in changed]
    before = corpus.base_texts([page.path for page in edited]) if edited else {}
    for page in edited:
        old = before[page.path]
        if old is not None and loader.visible_words(old) != loader.visible_words(page.text):
            altered = code_spans(html_source.parse(old)) != code_spans(page.tree)
            yield Finding("format.zh-stale", ERROR if altered else WARNING, page.path, 0,
                          f"the English {'code' if altered else 'text'} changed but {page.zh_path} did not.",
                          "update the Chinese page to match.", page.zh_path)


def check(corpus: Corpus) -> Iterable[Finding]:
    scoped = corpus.in_scope()
    registered_in_scope = {page.path for page in scoped}
    for page in scoped:
        yield from _page(page, corpus.nav)
    yield from _chinese_lags(corpus, scoped)
    if loader.NAV_FILE in corpus.scope.changed:
        # An edit to the navigation can drop any page.
        for page in corpus.pages:
            if page.path not in registered_in_scope and not _is_registered(page, corpus.nav):
                yield _unregistered(page)
    for path in corpus.orphans:
        if corpus.orphan_in_scope(path):
            yield _finding("format.orphan-zh", path, 0, "a Chinese page with no English page.",
                           f"add {path[:-len('.zh.html')]}.html, or delete this file.")
    for rel in sorted(corpus.files):
        path = "docs/" + rel
        if not corpus.path_in_scope(path) or corpus.nav.is_excluded(Path(rel)):
            continue
        if rel.endswith(".md") and not loader.is_generated(rel):
            yield _finding("format.markdown-source", path, 0, "a hand-written Markdown page.",
                           "convert it to HTML; only generated reference pages stay Markdown.")
        elif rel.endswith(".svg") and not rel.endswith(".zh.svg"):
            for line, text in checklang.prose_violations(corpus.read(path), markdown=False):
                yield _finding("format.cjk-in-english", path, line, f"Chinese text in an English figure: {text[:60]}.",
                               "move it to a .zh.svg.", text)
    if corpus.scope.mode == "all":
        yield from _stale_exemptions(corpus)
        present = {page.rel for page in corpus.pages}
        for rel in sorted(policy.PROTOTYPE_PAGES - present):
            yield _finding("format.prototype-stale", "scripts/docs_site/governance/policy.py", 0,
                           f"PROTOTYPE_PAGES lists {rel}, which does not exist.", "remove it from the list.", rel)


def check_docs_root(docs_root: Path) -> list[Finding]:
    """Every format error under one docs folder: what the build refuses."""
    scope = Scope("all", docs_root.parent)
    return list(check(loader.load(scope, docs_root)))

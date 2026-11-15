"""Internal links and #fragments, checked in the sources so no build is needed."""
from __future__ import annotations

import html
import json
import posixpath
import subprocess
from collections.abc import Iterable, Iterator
from urllib.parse import unquote

from .. import html_source
from . import corpus as loader
from .model import ERROR, WARNING, Corpus, Finding, Page

_SKIP = ("http://", "https://", "mailto:", "data:", "javascript:", "tel:", "//")
_DOCS_MOUNT = "/docs/"
# Files the build adds beside the pages.
_BUILT = ("assets/", "search-index.json", "sitemap.xml", "favicon.ico", "llms.txt", "index.html")


def _unique_slugger():
    seen: dict[str, int] = {}

    def slug(text: str) -> str:
        base = html_source.slugify(text)
        count = seen.get(base, 0)
        seen[base] = count + 1
        return base if count == 0 else f"{base}-{count}"
    return slug


def anchors(source: str) -> set[str]:
    """The ids a page will carry once built: written ones and heading anchors."""
    body = html_source.add_heading_ids(html_source.article_markup(source), _unique_slugger())
    found: set[str] = set()
    for node in html_source.walk(html_source.parse(body)):
        found.update(node.attrs[name] for name in ("id", "name") if node.attrs.get(name))
    return found


class _Resolver:
    def __init__(self, corpus: Corpus) -> None:
        self.corpus = corpus
        self.prototypes = {page.rel for page in corpus.pages if page.prototype}
        self.anchors: dict[str, set[str]] = {}
        try:
            redirects = json.loads(corpus.read("docs/redirects.json"))
        except (OSError, ValueError, subprocess.CalledProcessError):
            redirects = {}
        self.redirects = set(redirects)

    def target(self, source_rel: str, url: str) -> str | None:
        """The docs-relative target, or None when the link leaves the docs site."""
        if url.startswith(_DOCS_MOUNT):
            rel = url[len(_DOCS_MOUNT):]
        elif url.startswith("/"):
            return None
        else:
            rel = posixpath.normpath(posixpath.join(posixpath.dirname(source_rel), url))
        if rel == "" or rel == ".":
            rel = "README.html"
        return rel

    def exists(self, rel: str) -> bool:
        files = self.corpus.files
        rel = rel[:-len(".raw.html")] + ".html" if rel.endswith(".raw.html") else rel
        # The build copies docs/_static_root/ to the site root (latest.html).
        return (rel in files or rel in self.redirects or loader.is_generated(rel)
                or rel.startswith(_BUILT) or "_static_root/" + rel in files
                or rel[:-len(".html")] + ".md" in files
                or (rel.endswith(".md") and rel[:-len(".md")] + ".html" in files))

    def has_anchor(self, rel: str, fragment: str) -> bool:
        if rel.endswith(".raw.html"):
            rel = rel[:-len(".raw.html")] + ".html"
        if rel.replace(".zh.html", ".html") in self.prototypes or rel not in self.corpus.files or not rel.endswith(".html"):
            return True  # generated, redirected, or filled in by a page's own script
        if rel not in self.anchors:
            self.anchors[rel] = anchors(self.corpus.read("docs/" + rel))
        return unquote(html.unescape(fragment)) in self.anchors[rel]


def _links(tree: html_source.Node) -> Iterator[tuple[int, str, str]]:
    for node in html_source.walk(tree):
        for name in ("href", "src", "xlink:href"):
            raw = node.attrs.get(name)
            if not raw or raw.startswith(_SKIP) or "{" in raw:
                continue
            url, _, fragment = raw.partition("#")
            yield node.line, url.partition("?")[0], fragment


def _check(path: str, tree: html_source.Node, resolver: _Resolver, removed: set[str] | None,
           lost: dict[str, set[str]] | None = None) -> Iterator[Finding]:
    """Every link of a page, or with ``removed`` only those this change could have broken."""
    source_rel = path[len("docs/"):]
    foreign = resolver.corpus.foreign_dirty()
    for line, url, fragment in _links(tree):
        rel = resolver.target(source_rel, url) if url else source_rel
        if rel is None:
            continue
        if removed is not None and rel not in removed and fragment not in (lost or {}).get(rel, ()):
            continue
        severity = WARNING if "docs/" + rel in foreign else ERROR  # another session's page is not this change
        if rel.startswith("..") or not resolver.exists(rel):
            yield Finding("links.broken", severity, path, line, f"a link to {url}, which does not exist.",
                          "point it at an existing page; a repository file needs a GitHub URL.", url)
        elif fragment and not resolver.has_anchor(rel, fragment):
            yield Finding("links.fragment", severity, path, line, f"{url or 'this page'} has no #{fragment}.",
                          "link to an existing heading or id.", f"{url}#{fragment}")


def _lost_anchors(corpus: Corpus, scoped: list[Page], resolver: _Resolver) -> dict[str, set[str]]:
    """Ids a changed page carried before this change and no longer does."""
    if corpus.scope.mode == "all":
        return {}
    pairs = [(p.path, p.text) for p in scoped] + [(p.zh_path, p.zh_text) for p in scoped if p.zh_path]
    changed = set(corpus.scope.changed)
    pairs = [(path, text) for path, text in pairs if path in changed and path[len("docs/"):].replace(".zh.html", ".html") not in resolver.prototypes]
    before = corpus.base_texts([path for path, _text in pairs]) if pairs else {}
    lost: dict[str, set[str]] = {}
    for path, text in pairs:
        if before[path] is not None:
            gone = anchors(before[path]) - anchors(text)
            if gone:
                lost[path[len("docs/"):]] = gone
    return lost


REDIRECTS = "docs/redirects.json"


def _missing_redirects(corpus: Corpus, resolver: _Resolver, removed: set[str], scoped: list[Page]) -> Iterator[Finding]:
    """A removed page's old URL must keep working when the page moved."""
    english = sorted(rel for rel in removed if not rel.endswith(".zh.html") and rel not in resolver.redirects)
    if not english:
        return
    before = corpus.base_texts(["docs/" + rel for rel in english] + [page.path for page in scoped])
    added = [page for page in scoped if before[page.path] is None]
    for rel in english:
        old = before["docs/" + rel]
        if old is None:
            continue
        stem = posixpath.basename(rel)
        moved_to = next((page.rel for page in added
                         if posixpath.basename(page.rel) == stem and stem != "README.html"
                         or loader.visible_words(page.text) == loader.visible_words(old)), None)
        if moved_to:
            yield Finding("links.no-redirect", ERROR, REDIRECTS, 0, f"{rel} moved to {moved_to} with no redirect.",
                          f'add "{rel}": "{moved_to}" to docs/redirects.json.', rel)
        else:
            yield Finding("links.no-redirect", WARNING, REDIRECTS, 0, f"{rel} was removed and its URL now fails.",
                          "if a page replaces it, add the old path to docs/redirects.json.", rel)


def check(corpus: Corpus) -> Iterable[Finding]:
    resolver = _Resolver(corpus)
    scoped = corpus.in_scope()
    in_scope = {page.path for page in scoped}
    removed = set(corpus.removed_pages())
    lost = _lost_anchors(corpus, scoped, resolver)
    yield from _missing_redirects(corpus, resolver, removed, scoped)
    for page in corpus.pages:
        trees = [(page.path, page.tree)] + ([(page.zh_path, page.zh_tree)] if page.zh_tree else [])
        for path, tree in trees:
            if page.path in in_scope:
                yield from _check(path, tree, resolver, None)
            elif removed or lost:  # a deleted page or id breaks every page that links to it
                yield from _check(path, tree, resolver, removed, lost)

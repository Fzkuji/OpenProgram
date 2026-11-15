"""What a program can judge of the writing: few words, nothing said twice."""
from __future__ import annotations

import difflib
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import PurePosixPath
from types import SimpleNamespace

from .. import html_source, nav
from . import policy
from .model import ERROR, WARNING, Corpus, Finding, Page

_PROSE = {"p", "li", "dd", "blockquote"}
_NOT_PROSE = {"table", "figure", "pre", "svg", "head", "title", "meta", "nav"}
_NOT_PROSE_CLASS = {"flow", "fig-wide", "fig-narrow"}
_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_NO_RUNNING_TEXT = _HEADINGS | {"summary", "dt", "button", "label", "select", "option", "textarea",
                                "script", "style", "caption", "link", "hr", "img"}
_INLINE = {"a", "abbr", "b", "bdi", "bdo", "br", "cite", "code", "data", "del", "dfn", "em", "i", "ins",
           "kbd", "mark", "q", "s", "samp", "small", "span", "strong", "sub", "sup", "time", "u", "var", "wbr"}
_CONTAINERS = {"div", "section", "article", "main", "aside", "header", "footer", "body", "html"}
_SKIP_BLOCK = {"title", "meta", "link"}
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"[a-z0-9_][a-z0-9_\-./]*")
_STOP = frozenset("""a an the and or but if then else of to in on at by for from with as is are was were be been
being it its this that these those there here not no do does did so than into over under about only also can
may might must should would will shall has have had you your we our they their them he she his her which who
whom what when where why how each any all both more most such own same very just out up down off""".split())


@dataclass(frozen=True)
class _Sentence:
    page: str
    line: int
    text: str
    words: frozenset[str]


def _plain(node: html_source.Node | str) -> str:
    """Text of a node; a line break separates words the way a space does."""
    if isinstance(node, str):
        return node
    return " " if node.tag == "br" else "".join(_plain(child) for child in node.children)


def _text(node: html_source.Node | str) -> str:
    return re.sub(r"\s+", " ", _plain(node)).strip()


def _prose_blocks(node: html_source.Node) -> Iterator[tuple[int, str]]:
    """Each prose block once, as (line, text); a nested block is its own block.

    Text sitting directly in a container counts too: changing the tag must not
    change the verdict.
    """
    run: list[html_source.Node | str] = []
    line = node.line or 1

    def flush() -> Iterator[tuple[int, str]]:
        text = _text(html_source.Node("div", children=list(run)))
        run.clear()
        if text:
            yield line, text

    for child in node.children:
        if isinstance(child, str) or child.tag in _INLINE:
            if not run and not isinstance(child, str):
                line = child.line
            run.append(child)
            continue
        yield from flush()
        line = child.line or line
        if child.tag == "figure":
            for part in child.elements():
                if part.tag == "figcaption":
                    yield from _prose_blocks(html_source.Node("div", children=[html_source.Node("p", children=part.children, line=part.line)]))
            continue
        if child.tag in _NOT_PROSE or child.tag in _NO_RUNNING_TEXT or child.classes & _NOT_PROSE_CLASS:
            continue
        if child.tag not in _PROSE:
            yield from _prose_blocks(child)
            continue
        own = html_source.Node(child.tag, children=[
            part for part in child.children
            if isinstance(part, str) or not _holds_prose(part)])
        text = _text(own)
        if text:
            yield child.line, text
        for part in child.elements():
            if _holds_prose(part):
                yield from _prose_blocks(html_source.Node("div", children=[part]))
    yield from flush()


def _holds_prose(node: html_source.Node) -> bool:
    return node.tag in _PROSE | {"ul", "ol", "dl"} or node.tag in _NOT_PROSE


def _words(text: str) -> int:
    return len(text.split())


def content_words(text: str) -> frozenset[str]:
    tokens = (token.strip(".-/") for token in _WORD.findall(text.lower()))
    return frozenset(token for token in tokens if token and token not in _STOP)


def _sentences(path: str, blocks: list[tuple[int, str]]) -> list[_Sentence]:
    found = []
    for line, block in blocks:
        for text in _SENTENCE_END.split(block):
            words = content_words(text)
            if len(words) >= policy.SENTENCE_MIN_CONTENT_WORDS:
                found.append(_Sentence(path, line, text, words))
    return found


def _sentence_cells(tree: html_source.Node) -> list[tuple[int, str]]:
    """Table cells written as full sentences: they can repeat another page too."""
    cells = ((node.line, _text(node)) for node in html_source.walk(tree) if node.tag == "td")
    return [(line, text) for line, text in cells if text.endswith((".", "!", "?"))]


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b)


def _visible_words(page: Page) -> int:
    parts: list[str] = []
    html_source.visible_text(page.tree, parts)
    return _words(" ".join(parts))


def _band(warning: object, error: object) -> str:
    return f"warning above {warning}, error above {error}"


def _length(page: Page, blocks: list[tuple[int, str]]) -> Iterator[Finding]:
    error, warning = policy.PARAGRAPH_WORDS
    for line, text in blocks:
        count = _words(text)
        if count > warning:
            yield Finding("content.paragraph", ERROR if count > error else WARNING, page.path, line,
                          f"a paragraph of {count} words ({_band(warning, error)}).",
                          "split it, or turn the facts into a table or list.", text)
    total = sum(_words(text) for _line, text in blocks)
    error, warning = policy.PAGE_PROSE_WORDS
    if total > warning:
        yield Finding("content.page-prose", ERROR if total > error else WARNING, page.path, 0,
                      f"{total} words of prose ({_band(warning, error)}).",
                      "cut it, or move what can be looked up into tables, figures or a split page.",
                      "page-prose", total)
    if total >= policy.PROSE_SHARE_MIN_WORDS:
        visible = _visible_words(page)
        share = total / visible if visible else 0
        error, warning = policy.PROSE_SHARE
        if share > warning:
            yield Finding("content.prose-share", ERROR if share > error else WARNING, page.path, 0,
                          f"prose is {share:.0%} of this page ({_band(f'{warning:.0%}', f'{error:.0%}')}).",
                          "show the facts as a table or figure.", "prose-share", round(share, 3))


def _table_cells(page: Page) -> Iterator[Finding]:
    error, warning = policy.TABLE_CELL_WORDS
    for node in html_source.walk(page.tree):
        if node.tag == "td":
            text = _text(node)
            count = _words(text)
            if count > warning:
                yield Finding("content.table-cell", ERROR if count > error else WARNING, page.path, node.line,
                              f"a table cell of {count} words ({_band(warning, error)}).",
                              "keep cells to facts; move the explanation out, or split the row.", text)


def _duplicates_within(page: Page, sentences: list[_Sentence]) -> Iterator[Finding]:
    for index, sentence in enumerate(sentences):
        for earlier in sentences[:index]:
            if _jaccard(sentence.words, earlier.words) >= policy.SIMILAR_SENTENCE:
                yield Finding("content.duplicate-sentence", ERROR, page.path, sentence.line,
                              f"line {earlier.line} says the same: {sentence.text[:80]}",
                              "say it once.", sentence.text)
                break


def _table_covers(table: html_source.Node) -> Iterator[frozenset[str]]:
    """Each row with the column headers it answers to, and a short table as a whole."""
    rows = [node for node in html_source.walk(table) if node.tag == "tr"]
    heads = frozenset().union(*[content_words(_spaced_text(node)) for node in html_source.walk(table)
                                if node.tag == "thead"])
    covers = [content_words(_spaced_text(row)) | heads for row in rows]
    yield from covers
    if 2 <= len(rows) <= policy.SMALL_TABLE_ROWS:
        yield frozenset().union(*covers)


def _covering_texts(tree: html_source.Node) -> list[frozenset[str]]:
    covers: list[frozenset[str]] = []
    for node in html_source.walk(tree):
        if node.tag == "figure":
            # A caption sits inside its figure; it must not count as repeating itself.
            drawn = html_source.Node("figure", node.attrs, [part for part in node.children
                                                            if isinstance(part, str) or part.tag != "figcaption"])
            covers.append(content_words(_spaced_text(drawn)))
        elif node.tag == "table":
            covers.extend(_table_covers(node))
    return covers


def _spaced_text(node: html_source.Node | str) -> str:
    """Text with block boundaries kept, plus any figure description."""
    if isinstance(node, str):
        return node
    label = node.attrs.get("aria-label", "")
    return " ".join([label, *(_spaced_text(child) for child in node.children)])


def _repeats_figure(page: Page, sentences: list[_Sentence]) -> Iterator[Finding]:
    covers = _covering_texts(page.tree)
    if not covers:
        return
    error, warning = policy.COVERED_SENTENCE
    lookup_lines = {node.line for node in html_source.walk(page.tree) if node.tag == "dd"}
    for sentence in sentences:
        if sentence.line in lookup_lines:  # a definition list is itself the table
            continue
        best = max(len(sentence.words & cover) / len(sentence.words) for cover in covers)
        if best >= warning:
            yield Finding("content.repeats-figure", ERROR if best >= error else WARNING, page.path, sentence.line,
                          f"a table row or figure already says this: {sentence.text[:80]}",
                          "delete the sentence, or keep only what the table or figure cannot show.", sentence.text)


def _bullet_sections(page: Page) -> Iterator[Finding]:
    error, warning = policy.BULLET_SECTION_ITEMS

    def close(heading: html_source.Node | None, blocks: list) -> Iterator[Finding]:
        kept = [b for b in blocks if not (b.tag == "p" and b.classes & {"note", "next"})]
        if not kept or any(b.tag not in {"ul", "ol"} or b.classes & _NOT_PROSE_CLASS for b in kept):
            return
        items = sum(1 for block in kept for child in block.elements() if child.tag == "li")
        if items >= warning:
            name = _text(heading) if heading is not None else ""
            yield Finding("content.bullet-section", ERROR if items >= error else WARNING, page.path,
                          heading.line if heading is not None else kept[0].line,
                          f'section "{name}" is {items} bare bullets.',
                          "turn it into a table with a column for what differs, or group it.", name)

    def flow(node: html_source.Node) -> Iterator[html_source.Node]:
        """Blocks in reading order: a wrapping div or section changes nothing."""
        for child in node.elements():
            if child.tag in _CONTAINERS and not child.classes & (_NOT_PROSE_CLASS | {"note", "next"}):
                yield from flow(child)
            else:
                yield child

    heading = None
    blocks: list[html_source.Node] = []
    for child in flow(page.tree):
        if child.tag in _SKIP_BLOCK:
            continue
        if child.tag in _HEADINGS:
            yield from close(heading, blocks)
            heading, blocks = child, []
        else:
            blocks.append(child)
    yield from close(heading, blocks)


def _only_link(details: html_source.Node) -> html_source.Node | None:
    """The single link that is all a fold holds, if it is."""
    body = [child for child in details.children
            if (isinstance(child, str) and child.strip()) or (not isinstance(child, str) and child.tag != "summary")]
    if len(body) != 1 or isinstance(body[0], str):
        return None
    node = body[0]
    if node.tag == "p" and len(list(node.elements())) == 1 and not "".join(c for c in node.children if isinstance(c, str)).strip():
        node = list(node.elements())[0]
    return node if node.tag == "a" else None


def _hollow_folds(page: Page) -> Iterator[Finding]:
    """A fold whose body only repeats its summary as a link says one thing twice."""
    for node in html_source.walk(page.tree):
        if node.tag != "details":
            continue
        link = _only_link(node)
        summary = next((child for child in node.elements() if child.tag == "summary"), None)
        if link is None or summary is None:
            continue
        said, opens = _text(summary).lower(), _text(link).lower()
        if difflib.SequenceMatcher(None, said, opens).ratio() >= policy.FOLD_REPEATS_SUMMARY:
            yield Finding("content.hollow-fold", WARNING, page.path, node.line,
                          f'this fold only repeats its summary as a link: "{_text(link)[:60]}".',
                          "drop the fold and make the entry one direct link.", _text(link))


class _SentenceIndex:
    """Candidates sharing a rare word: prefix filtering for Jaccard similarity."""

    def __init__(self, sentences: list[_Sentence]) -> None:
        self.frequency = Counter(word for s in sentences for word in s.words)
        self.postings: dict[str, list[_Sentence]] = defaultdict(list)
        for sentence in sentences:
            for word in self._prefix(sentence.words):
                self.postings[word].append(sentence)

    def _prefix(self, words: frozenset[str]) -> list[str]:
        keep = len(words) - int(len(words) * policy.SIMILAR_SENTENCE + 0.9999) + 1
        return sorted(words, key=lambda word: (self.frequency[word], word))[:keep]

    def similar(self, sentence: _Sentence) -> Iterator[_Sentence]:
        seen: set[int] = set()
        for word in self._prefix(sentence.words):
            for other in self.postings[word]:
                if id(other) in seen or other.page == sentence.page:
                    continue
                seen.add(id(other))
                if _jaccard(sentence.words, other.words) >= policy.SIMILAR_SENTENCE:
                    yield other


def _uniform_tabs(corpus: Corpus) -> Iterator[Finding]:
    tabs: dict[str, list[tuple]] = defaultdict(list)
    for page in corpus.pages:
        tab = nav.tab_of(SimpleNamespace(rel=PurePosixPath(page.rel), key=page.key))
        tabs[tab].append(tuple(
            child.tag for child in page.tree.elements()
            if child.tag not in _SKIP_BLOCK and not child.classes & {"note", "next"}))
    error, warning = policy.UNIFORM_TAB
    for tab, shapes in sorted(tabs.items()):
        if len(shapes) < policy.UNIFORM_TAB_MIN_PAGES:
            continue
        shape, count = Counter(shapes).most_common(1)[0]
        share = count / len(shapes)
        if share > warning:
            yield Finding("content.uniform-tab", ERROR if share > error else WARNING, f"docs/{tab}", 0,
                          f"{share:.0%} of this tab's pages share one block sequence: {' '.join(shape[:8])}.",
                          "pick each page's form from its content.", tab)


def check(corpus: Corpus) -> Iterable[Finding]:
    blocks = {page.path: list(_prose_blocks(page.tree)) for page in corpus.pages}
    sentences = {page.path: _sentences(page.path, blocks[page.path]) for page in corpus.pages}
    across = {page.path: sentences[page.path] + _sentences(page.path, _sentence_cells(page.tree))
              for page in corpus.pages}
    index = _SentenceIndex([s for items in across.values() for s in items])
    for page in corpus.in_scope():
        if page.prototype:
            continue
        yield from _length(page, blocks[page.path])
        yield from _duplicates_within(page, sentences[page.path])
        yield from _repeats_figure(page, sentences[page.path])
        yield from _bullet_sections(page)
        yield from _table_cells(page)
        yield from _hollow_folds(page)
        foreign = corpus.foreign_dirty()
        for sentence in across[page.path]:
            matches = list(index.similar(sentence))
            other = next((match for match in matches if match.page not in foreign), matches[0] if matches else None)
            if other is not None:
                yield Finding("content.duplicate-across-pages", WARNING if other.page in foreign else ERROR,
                              page.path, sentence.line,
                              f"{other.page}:{other.line} says the same: {sentence.text[:80]}",
                              "keep it on one page and link there from the other.", sentence.text)
    if corpus.scope.mode == "all":
        yield from _uniform_tabs(corpus)

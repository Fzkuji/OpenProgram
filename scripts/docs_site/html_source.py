"""HTML document sources: pages written as plain HTML on the shared foundation.

A source is rendered directly inside the site chrome. Only a page listed in
``governance.policy.PROTOTYPE_PAGES`` carries its own script and styles.

This module turns one source into what the build needs: the article body with
heading anchors, its description and search text, and a Markdown rendition for
Agents. It parses with the standard library so the docs build gains no new
dependency, and it never re-serializes the author's markup.
"""

from __future__ import annotations

import html as _html
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
         "source", "track", "wbr"}
# Elements the HTML parser closes implicitly when a sibling opens.
_CLOSED_BY_SIBLING = {
    "li": {"li"}, "dt": {"dt", "dd"}, "dd": {"dt", "dd"}, "tr": {"tr"},
    "td": {"td", "th", "tr"}, "th": {"td", "th", "tr"},
    "p": {"p", "ul", "ol", "dl", "table", "pre", "figure", "details", "div",
          "h1", "h2", "h3", "h4", "h5", "h6"},
}


@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["Node | str"] = field(default_factory=list)
    line: int = 0

    @property
    def classes(self) -> set[str]:
        return set(self.attrs.get("class", "").split())

    def elements(self) -> list["Node"]:
        return [child for child in self.children if isinstance(child, Node)]


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs) -> None:
        closes = _CLOSED_BY_SIBLING
        while len(self.stack) > 1 and tag in closes.get(self.stack[-1].tag, ()):
            self.stack.pop()
        node = Node(tag, {name: value or "" for name, value in attrs}, line=self.getpos()[0])
        self.stack[-1].children.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.stack[-1].children.append(Node(tag, {name: value or "" for name, value in attrs}, line=self.getpos()[0]))

    def handle_endtag(self, tag: str) -> None:
        for depth in range(len(self.stack) - 1, 0, -1):
            if self.stack[depth].tag == tag:
                del self.stack[depth:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def _parse(source: str) -> Node:
    builder = _TreeBuilder()
    builder.feed(source)
    builder.close()
    return builder.root


def _walk(node: Node):
    for child in node.elements():
        yield child
        yield from _walk(child)


parse = _parse
walk = _walk


def slugify(text: str) -> str:
    """The anchor a heading gets."""
    text = re.sub(r"<[^>]+>", "", text).strip().lower()
    text = re.sub(r"[^\w一-鿿\- ]+", "", text)
    return re.sub(r"[\s]+", "-", text).strip("-") or "section"


def is_html_document(source: str) -> bool:
    """True when the source carries no styling or script of its own."""
    for node in _walk(_parse(source)):
        if node.tag in {"style", "script"} or "style" in node.attrs:
            return False
        if node.tag == "link" and "stylesheet" in node.attrs.get("rel", "").lower():
            return False
    return True


# ── article body ────────────────────────────────────────────────────────────

_COMMENT = re.compile(r"<!--.*?-->", re.S)
_HEAD = re.compile(r"<head\b.*?</head\s*>", re.S | re.I)
_BODY = re.compile(r"<body\b[^>]*>(.*)</body\s*>", re.S | re.I)
_MAIN = re.compile(r"<main\b[^>]*>(.*)</main\s*>", re.S | re.I)
_SKIP_LINK = re.compile(r'<a\b[^>]*\bclass="skip"[^>]*>.*?</a\s*>', re.S | re.I)
_OUTER_TAGS = re.compile(r"<!doctype[^>]*>|</?html\b[^>]*>", re.I)
_LEADING_META = re.compile(r"^\s*(?:<title\b[^>]*>.*?</title\s*>|<meta\b[^>]*>|<link\b[^>]*>)", re.S | re.I)
_HEADING_OR_FOLD = re.compile(
    r"<(?P<close>/?)(?:details\b[^>]*|h(?P<level>[2-6])\b(?P<attrs>[^>]*))>", re.I)
_ID_ATTR = re.compile(r"""\bid\s*=\s*["']([^"']+)["']""", re.I)


def article_markup(source: str) -> str:
    """The author's content, without any document wrapper around it."""
    text = _COMMENT.sub("", source)
    text = _HEAD.sub("", text)
    body = _BODY.search(text)
    if body:
        text = body.group(1)
    main = _MAIN.search(text)
    if main:
        text = main.group(1)
    text = _SKIP_LINK.sub("", _OUTER_TAGS.sub("", text))
    while True:
        stripped = _LEADING_META.sub("", text, count=1)
        if stripped == text:
            return text.strip()
        text = stripped


def add_heading_ids(body: str, slugify: Callable[[str], str]) -> str:
    """Give every heading outside a fold an anchor so it reaches the contents.

    Author-written ids are kept. Headings inside a closed ``<details>`` get no
    generated anchor: a contents entry there would point at something hidden.
    """
    out: list[str] = []
    position = 0
    fold_depth = 0
    for match in _HEADING_OR_FOLD.finditer(body):
        if match.group("level") is None:
            fold_depth += -1 if match.group("close") else 1
            continue
        if match.group("close") or fold_depth or _ID_ATTR.search(match.group("attrs")):
            continue
        level = match.group("level")
        end = body.find(f"</h{level}", match.end())
        if end < 0:
            continue
        text = _html.unescape(re.sub(r"<[^>]+>", "", body[match.end():end])).strip()
        if not text:
            continue
        out.append(body[position:match.start()])
        out.append(f'<h{level} id="{_html.escape(slugify(text), quote=True)}"{match.group("attrs")}>')
        position = match.end()
    out.append(body[position:])
    return "".join(out)


# ── reading order shared by search text and the Agent rendition ─────────────

def _skipped(node: Node, parent: Node | None) -> bool:
    """A wide figure with a narrow twin says the same thing twice; keep the twin."""
    if "fig-wide" not in node.classes or parent is None:
        return False
    return any("fig-narrow" in sibling.classes for sibling in parent.elements())


def _text_of(node: Node | str) -> str:
    if isinstance(node, str):
        return node
    return "".join(_text_of(child) for child in node.children)


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


text_of = _text_of


def _figure_text(svg: Node) -> str:
    return _collapse(svg.attrs.get("aria-label", "") or _text_of(svg))


def _visible_text(node: Node, out: list[str]) -> None:
    for child in node.children:
        if isinstance(child, str):
            out.append(child)
        elif _skipped(child, node):
            continue
        elif child.tag == "svg":
            out.extend((" ", _figure_text(child), " "))
        else:
            out.append(" ")
            _visible_text(child, out)
            out.append(" ")


visible_text = _visible_text


def page_text(body: str, *, limit: int = 2000) -> str:
    """Search text: what a reader sees, with each figure counted once."""
    parts: list[str] = []
    _visible_text(_parse(body), parts)
    return _collapse("".join(parts))[:limit]


def page_description(source: str, body: str, *, limit: int = 160) -> str:
    """The author's description, or the opening text after the heading."""
    for node in _walk(_parse(_HEAD.search(source).group(0) if _HEAD.search(source) else source)):
        if node.tag == "meta" and node.attrs.get("name", "").lower() == "description":
            written = _collapse(node.attrs.get("content", ""))
            if written:
                return written
    tree = _parse(body)
    tree.children = [child for child in tree.children
                     if not (isinstance(child, Node) and child.tag == "h1")]
    parts: list[str] = []
    _visible_text(tree, parts)
    text = _collapse("".join(parts))
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0]


# ── Markdown rendition ──────────────────────────────────────────────────────

_BLOCK = {"p", "ul", "ol", "dl", "table", "pre", "figure", "details", "div", "section",
          "article", "aside", "nav", "blockquote", "hr", "svg",
          "h1", "h2", "h3", "h4", "h5", "h6"}


def _code_span(text: str) -> str:
    text = _collapse(text)
    if not text:
        return ""
    run = max((len(found) for found in re.findall(r"`+", text)), default=0)
    fence = "`" * (run + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _inline(node: Node | str, origin: tuple[str, str]) -> str:
    if isinstance(node, str):
        return re.sub(r"\s+", " ", node)
    children = "".join(_inline(child, origin) for child in node.children)
    if node.tag in {"code", "kbd", "samp"}:
        return _code_span(_text_of(node))
    if node.tag in {"strong", "b"}:
        return f"**{children.strip()}**" if children.strip() else ""
    if node.tag in {"em", "i"}:
        return f"*{children.strip()}*" if children.strip() else ""
    if node.tag == "a":
        href = node.attrs.get("href", "")
        link_base, public_base = origin
        if link_base and public_base and href.startswith(link_base):
            href = public_base + href[len(link_base):]
        return f"[{children.strip()}]({href})" if href else children
    if node.tag == "br":
        return " "
    if node.tag == "img":
        return f'![{node.attrs.get("alt", "")}]({node.attrs.get("src", "")})'
    if node.tag == "svg":
        return _figure_text(node)
    if "arg" in node.classes:
        return f" ({children.strip()})"
    if node.tag == "small":
        return f" {children.strip()} "
    return children


def _inline_all(nodes: list[Node | str], origin: tuple[str, str]) -> str:
    return _collapse("".join(_inline(node, origin) for node in nodes))


def _table(node: Node, origin: tuple[str, str]) -> list[str]:
    blocks: list[str] = []
    rows: list[list[str]] = []
    head_rows = 0
    for part in node.elements():
        if part.tag == "caption":
            blocks.append(_inline_all(part.children, origin))
            continue
        group = part.elements() if part.tag in {"thead", "tbody", "tfoot"} else [part]
        for row in group:
            if row.tag != "tr":
                continue
            cells: list[str] = []
            for cell in row.elements():
                text = _inline_all(cell.children, origin).replace("|", "\\|")
                # A spanning cell applies to each column it covers.
                cells.extend([text] * max(int(cell.attrs.get("colspan") or 1), 1))
            rows.append(cells)
            head_rows += part.tag == "thead"
    if not rows:
        return blocks
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    header = rows[0] if head_rows else [""] * width
    body = rows[1:] if head_rows else rows
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * width) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    blocks.append("\n".join(lines))
    return blocks


def _definition_list(node: Node, origin: tuple[str, str]) -> list[str]:
    lines: list[str] = []
    term = ""
    for child in node.elements():
        if child.tag == "dt":
            term = _inline_all(child.children, origin)
        elif child.tag == "dd":
            value = _inline_all(child.children, origin)
            lines.append(f"- **{term}**: {value}" if term else f"- {value}")
    return ["\n".join(lines)] if lines else []


def _list(node: Node, origin: tuple[str, str]) -> list[str]:
    lines: list[str] = []
    for number, item in enumerate((child for child in node.elements() if child.tag == "li"), 1):
        marker = f"{number}. " if node.tag == "ol" else "- "
        content = "\n\n".join(_blocks(item, origin)) or ""
        indent = " " * len(marker)
        lines.append(marker + content.replace("\n", "\n" + indent))
    return ["\n".join(lines)] if lines else []


def _preformatted(node: Node) -> str:
    code = next((child for child in node.elements() if child.tag == "code"), None)
    language = ""
    if code is not None:
        language = next((name[9:] for name in code.classes if name.startswith("language-")), "")
    text = _text_of(node).rstrip("\n")
    run = max((len(found) for found in re.findall(r"`{3,}", text)), default=2)
    fence = "`" * (run + 1)
    return f"{fence}{language}\n{text}\n{fence}"


def _blocks(node: Node, origin: tuple[str, str]) -> list[str]:
    """Render a container: consecutive inline children form one paragraph."""
    blocks: list[str] = []
    pending: list[Node | str] = []

    def flush() -> None:
        text = _inline_all(pending, origin)
        if text:
            blocks.append(text)
        pending.clear()

    for child in node.children:
        if isinstance(child, str) or child.tag not in _BLOCK:
            pending.append(child)
            continue
        flush()
        if _skipped(child, node):
            continue
        if child.tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            blocks.append("#" * int(child.tag[1]) + " " + _inline_all(child.children, origin))
        elif child.tag == "p":
            text = _inline_all(child.children, origin)
            if text:
                blocks.append(text)
        elif child.tag in {"ul", "ol"}:
            blocks.extend(_list(child, origin))
        elif child.tag == "dl":
            blocks.extend(_definition_list(child, origin))
        elif child.tag == "table":
            blocks.extend(_table(child, origin))
        elif child.tag == "pre":
            blocks.append(_preformatted(child))
        elif child.tag == "svg":
            text = _figure_text(child)
            if text:
                blocks.append(text)
        elif child.tag == "hr":
            blocks.append("---")
        elif child.tag == "blockquote":
            inner = "\n\n".join(_blocks(child, origin))
            blocks.append("\n".join("> " + line for line in inner.split("\n")))
        elif child.tag == "details":
            summary = next((part for part in child.elements() if part.tag == "summary"), None)
            if summary is not None:
                blocks.append(f"**{_inline_all(summary.children, origin)}**")
            rest = Node("div", children=[part for part in child.children if part is not summary])
            blocks.extend(_blocks(rest, origin))
        else:
            blocks.extend(_blocks(child, origin))
    flush()
    return blocks


def to_markdown(body: str, *, link_base: str = "", public_base: str = "") -> str:
    """A clean Markdown copy of an article, for Agents and other plain readers.

    Figures appear once, through their narrow twin or accessible description.
    Folded detail is included unfolded. Links under the build's mount path
    ``link_base`` become absolute under the public ``public_base``, so the
    copy works wherever it is read.
    """
    return "\n\n".join(_blocks(_parse(body), (link_base, public_base))) + "\n"

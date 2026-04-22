"""web_fetch tool — pull a URL and return its readable contents.

Fetches HTTP/HTTPS pages, then:

* HTML → readable text. Uses ``trafilatura`` when installed (much
  cleaner than naive stripping, keeps heading / list structure). Falls
  back to a small built-in tag stripper so the tool still works on a
  machine without the optional dep.
* JSON / plain text / markdown → passed through verbatim.
* Everything else (images, PDFs, binary) → short descriptor; caller
  should use ``pdf`` or ``image_analyze`` for those formats.

Size cap (5 MB) and timeout (30 s default) mirror opencode / claude-code
defaults. Spoofed Chrome UA because plenty of sites 403 non-browser UAs
for no good reason; if the target Cloudflare-challenges us we retry
once with an honest UA so at least we don't look like we're hiding.

Design choices:

* Stdlib-only fast path. ``urllib.request`` handles redirects, SSL,
  compression when you ask. No ``requests`` dep.
* Extraction library is optional. ``trafilatura`` is the best text
  extractor I've found for agent use (keeps structure without cruft),
  but users who don't install it still get usable output.
* Returns STR even on error. Agents can read ``"Error: …"`` and react;
  raising makes the tool loop abort the turn.
"""

from __future__ import annotations

import gzip
import html
import io
import re
import urllib.error
import urllib.request
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

from .._helpers import read_bool_param, read_int_param, read_string_param


NAME = "web_fetch"

# 5 MB mirrors opencode. Plenty for text-heavy pages; HTML that pushes
# past this is usually an attack surface (giant payloads, redirect bombs)
# we'd rather truncate than crash on.
MAX_BYTES = 5 * 1024 * 1024

# 30 s is a reasonable "one slow server" ceiling. Raised to 120 s max via
# the tool arg. Below 5 s and you'll miss plenty of legitimate sites.
DEFAULT_TIMEOUT = 30.0
MAX_TIMEOUT = 120.0

# Default truncation for returned text. Agent context windows can handle
# 50 KB without flinching; longer pages get a clear truncation note so the
# model knows to refine its prompt if it needs the tail.
DEFAULT_MAX_CHARS = 50_000

# Spoofed UA — same reasoning as opencode / claude-code: too many sites
# reject "python-requests" or empty UAs despite their robots.txt being
# perfectly fine. If Cloudflare challenges us we retry with our real UA
# (see _fetch_with_cf_fallback).
CHROME_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
)
HONEST_UA = "openprogram-web-fetch/1.0"


DESCRIPTION = (
    "Fetch a web page and return its readable text. Follows redirects, "
    "handles gzip, strips scripts/styles. Pass `format=markdown` (default) "
    "to get structured text with headings, `format=text` for plain text, "
    "or `format=html` for the raw HTML. Non-HTML content types (json, txt, "
    "markdown) are returned as-is. Images / PDFs / binary are not supported "
    "— use `image_analyze` or `pdf` for those.\n"
    "\n"
    "Use this when you already know which URL to read. Use `web_search` "
    "first if you only have a question and need to discover URLs."
)


SPEC: dict[str, Any] = {
    "name": NAME,
    "description": DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Absolute http:// or https:// URL to fetch.",
            },
            "format": {
                "type": "string",
                "enum": ["markdown", "text", "html"],
                "description": "Output format. markdown (default) keeps headings & lists; text strips all structure; html returns the raw body.",
            },
            "max_chars": {
                "type": "integer",
                "description": f"Truncate returned text to this many characters (default {DEFAULT_MAX_CHARS}). A truncation note is appended when truncation occurs.",
            },
            "timeout": {
                "type": "number",
                "description": f"Request timeout in seconds (default {DEFAULT_TIMEOUT}, max {MAX_TIMEOUT}).",
            },
            "include_links": {
                "type": "boolean",
                "description": "When format=markdown, keep <a href> URLs inline as [text](url). Default true.",
            },
        },
        "required": ["url"],
    },
}


class _StripTagsParser(HTMLParser):
    """Tiny fallback HTML → text extractor.

    Not as clean as trafilatura. Used only when trafilatura isn't
    installed. Keeps rough paragraph breaks, drops script/style/nav
    noise, and writes [link](url) for anchors when asked.
    """

    # Void elements (meta/link/img/etc.) aren't in _DROP because
    # HTMLParser doesn't fire handle_endtag for them — they'd leave
    # drop_depth stuck at 1 and swallow the rest of the document.
    # They have no text content anyway, so skipping them doesn't matter.
    _DROP = {"script", "style", "noscript", "iframe", "head"}
    _BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "pre"}

    def __init__(self, keep_links: bool) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._drop_depth = 0
        self._pending_href: str | None = None
        self._anchor_buf: list[str] | None = None
        self._keep_links = keep_links

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self._DROP:
            self._drop_depth += 1
            return
        if tag in self._BLOCK:
            self._out.append("\n")
        if self._keep_links and tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._pending_href = href
                self._anchor_buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag in self._DROP and self._drop_depth > 0:
            self._drop_depth -= 1
            return
        if self._keep_links and tag == "a" and self._anchor_buf is not None:
            text = "".join(self._anchor_buf).strip()
            if text and self._pending_href:
                self._out.append(f"[{text}]({self._pending_href})")
            elif text:
                self._out.append(text)
            self._pending_href = None
            self._anchor_buf = None
        if tag in self._BLOCK:
            self._out.append("\n")

    def handle_data(self, data: str) -> None:
        if self._drop_depth > 0:
            return
        if self._anchor_buf is not None:
            self._anchor_buf.append(data)
            return
        self._out.append(data)

    def result(self) -> str:
        text = "".join(self._out)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n[ \t]+", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def _strip_with_trafilatura(html_body: str, fmt: str) -> str | None:
    """Return trafilatura's extraction or None if the lib isn't importable.

    ``output_format="markdown"`` keeps headings and lists; "txt" is plain.
    We only call this for HTML bodies — JSON/text bodies skip it.
    """
    try:
        import trafilatura  # type: ignore
    except Exception:
        return None
    try:
        return trafilatura.extract(
            html_body,
            output_format="markdown" if fmt == "markdown" else "txt",
            include_links=True,
            include_tables=True,
        )
    except Exception:
        return None


def _build_request(url: str, ua: str) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.9,text/plain;q=0.8,*/*;q=0.5",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip",
        },
    )


def _read_body(resp: Any) -> tuple[bytes, bool]:
    """Read response body honouring Content-Length + our size cap.

    Returns (body_bytes, truncated). Decompresses gzip when signalled.
    """
    declared = resp.headers.get("Content-Length")
    if declared:
        try:
            if int(declared) > MAX_BYTES:
                return b"", True  # too big to even start
        except (TypeError, ValueError):
            pass
    raw = resp.read(MAX_BYTES + 1)
    truncated = len(raw) > MAX_BYTES
    if truncated:
        raw = raw[:MAX_BYTES]
    if resp.headers.get("Content-Encoding", "").lower() == "gzip":
        try:
            raw = gzip.decompress(raw)
        except Exception:
            # If the server lied about gzip we still want to keep the
            # raw bytes — downstream decoders might handle it.
            pass
    return raw, truncated


def _fetch_with_cf_fallback(url: str, timeout: float) -> tuple[Any, bytes, bool]:
    """Fetch ``url`` trying Chrome UA first, honest UA on Cloudflare 403.

    Cloudflare emits ``cf-mitigated: challenge`` for bot-challenged
    responses. If we see that with a 403, retry once with our honest UA
    — being transparent is the right move when the spoof doesn't buy us
    anything anyway.
    """
    last_err: Exception | None = None
    for ua in (CHROME_UA, HONEST_UA):
        req = _build_request(url, ua)
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
            body, truncated = _read_body(resp)
            return resp, body, truncated
        except urllib.error.HTTPError as e:
            last_err = e
            mitigated = e.headers.get("cf-mitigated", "").lower() if e.headers else ""
            if e.code == 403 and mitigated == "challenge" and ua == CHROME_UA:
                # Retry with honest UA.
                continue
            raise
        except Exception as e:
            last_err = e
            raise
    assert last_err is not None
    raise last_err


def _decode_body(body: bytes, content_type: str) -> str:
    """Decode bytes using the charset from Content-Type (or utf-8 fallback)."""
    charset = "utf-8"
    m = re.search(r"charset=([\w\-]+)", content_type or "", re.I)
    if m:
        charset = m.group(1)
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    head = text[:max_chars]
    remaining = len(text) - max_chars
    return f"{head}\n\n…[truncated {remaining:,} chars; narrow your request or fetch a specific section]"


def execute(
    url: str | None = None,
    format: str = "markdown",
    max_chars: int | None = None,
    timeout: float | None = None,
    include_links: bool = True,
    **kw: Any,
) -> str:
    # Tolerate model-side camelCase / title-case variants
    if url is None:
        url = read_string_param(kw, "url", "Url", "URL")
    fmt = read_string_param(kw, "format", default=format) or format
    max_chars = read_int_param(kw, "max_chars", "maxChars", default=max_chars or DEFAULT_MAX_CHARS)
    timeout_val = float(
        read_int_param(kw, "timeout", "Timeout") or (timeout if timeout is not None else DEFAULT_TIMEOUT)
    )
    include_links_val = read_bool_param(kw, "include_links", "includeLinks", default=include_links)

    if not url:
        return "Error: `url` is required."
    if not isinstance(url, str):
        return f"Error: `url` must be a string, got {type(url).__name__}."
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"Error: only http:// and https:// URLs are supported, got {parsed.scheme!r}."
    timeout_val = max(1.0, min(timeout_val, MAX_TIMEOUT))
    fmt_norm = (fmt or "markdown").lower()
    if fmt_norm not in ("markdown", "text", "html"):
        return f"Error: `format` must be one of markdown/text/html, got {fmt!r}."

    try:
        resp, body, size_truncated = _fetch_with_cf_fallback(url, timeout_val)
    except urllib.error.HTTPError as e:
        return f"Error: HTTP {e.code} {e.reason} for {url}"
    except urllib.error.URLError as e:
        return f"Error: network error for {url}: {getattr(e, 'reason', e)}"
    except Exception as e:
        return f"Error: failed to fetch {url}: {type(e).__name__}: {e}"

    content_type = (resp.headers.get("Content-Type") or "").lower()
    final_url = getattr(resp, "url", url)
    try:
        text = _decode_body(body, content_type)
    except Exception as e:
        return f"Error: cannot decode {final_url}: {type(e).__name__}: {e}"

    size_note = (
        f"\n\n…[response exceeded {MAX_BYTES // 1024 // 1024} MB cap, truncated]" if size_truncated else ""
    )

    # Route by content type
    is_html = "html" in content_type or "<html" in text[:512].lower()
    if is_html:
        if fmt_norm == "html":
            out = text
        else:
            extracted = _strip_with_trafilatura(text, fmt_norm) if fmt_norm == "markdown" else None
            if extracted:
                out = extracted
            else:
                # Fallback stripper — always works, less pretty.
                parser = _StripTagsParser(keep_links=include_links_val and fmt_norm == "markdown")
                try:
                    parser.feed(text)
                    parser.close()
                except Exception:
                    pass
                out = parser.result()
                out = html.unescape(out)
    elif "json" in content_type:
        out = text  # leave structured as-is
    elif content_type.startswith("text/") or "xml" in content_type:
        out = text
    elif not content_type:
        # Unknown — give the agent the raw body but label it clearly.
        out = text
    else:
        return (
            f"Error: unsupported Content-Type {content_type!r} for {final_url}. "
            f"Use `pdf` for PDFs or `image_analyze` for images."
        )

    header = f"# {final_url}\n(content-type: {content_type or 'unknown'})\n\n"
    return header + _truncate(out, max_chars) + size_note


__all__ = ["NAME", "SPEC", "execute", "DESCRIPTION"]

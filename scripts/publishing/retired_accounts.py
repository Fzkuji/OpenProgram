"""Find mentions of the project's retired GitHub accounts.

The project once published under an account it no longer has (its first
account, retired for a while, is in use again and is not listed). Nothing that is
published may name them again: no author line, link, example, fixture or note.
The names are kept out of this file as well, so the guard is not itself a
mention: each one is stored as the SHA-256 of its lowercase form, and text is
compared token by token.

A token is a run of ``A-Z a-z 0-9 - _ . @ +``; each token is also split on
``/ @ + .`` and, separately, on ``/ @ + . _ -``, so a login inside an e-mail
address, a URL or a file name is found too. Percent-encoded (also
twice), HTML-entity and ``\\u``/``\\x``-escaped text is decoded first.

Usage:
    python scripts/publishing/retired_accounts.py              # the tracked files of this checkout
    python scripts/publishing/retired_accounts.py --commit REF # the tree of a commit
    python scripts/publishing/retired_accounts.py --stdin      # text on standard input

Exit status 1 lists every ``path:line`` that names a retired account.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import re
import subprocess
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path
from urllib.parse import unquote

# SHA-256 of the lowercase identifiers: the retired login, its numeric
# account id and a second login used in examples.
RETIRED = frozenset({
    "e3ab38a26b51d34c8a98fa6a8ca6c38125468dc2383372ca80262095ba903057",
    "6eeaef9f8da37480386e09f2f9d6539cf89f99148c867542c932d98b83f1f013",
    "e7e7ee0a956ddf50328fd113f5b9c472390e600d7dfa0f06016b9731fbbe2656",
})

# (length, SHA-256) of a login that may not appear even inside a longer
# word. None now: the project's first account is in use again, and the
# retired login is found as a whole token.
STEMS: frozenset[tuple[int, str]] = frozenset()

_TOKEN = re.compile(r"[a-z0-9\-_.@+]+")
_COARSE = re.compile(r"[/@+.]+")
_FINE = re.compile(r"[/@+._\-]+")
_MAX_BYTES = 16 * 1024 * 1024
_seen: dict[str, bool] = {}
_clean: set[str] = set()  # tokens already found to name nothing


def _retired(token: str) -> bool:
    known = _seen.get(token)
    if known is None:
        known = hashlib.sha256(token.encode("utf-8")).hexdigest() in RETIRED
        _seen[token] = known
    return known


_ESCAPE = re.compile(r"\\(?:u([0-9a-f]{4})|x([0-9a-f]{2}))")


def _unescape(match: re.Match[str]) -> str:
    return chr(int(match.group(1) or match.group(2), 16))


def _tokens(text: str) -> set[str]:
    lowered = text.lower()
    found = set(_TOKEN.findall(lowered))
    decoded = lowered
    for _ in range(3):
        if "%" not in decoded:
            break
        decoded = unquote(decoded)
        found.update(_TOKEN.findall(decoded))
    if "&" in lowered:
        found.update(_TOKEN.findall(html.unescape(lowered).lower()))
    if "\\" in lowered:
        found.update(_TOKEN.findall(_ESCAPE.sub(_unescape, lowered).lower()))
    return found


def _contains_stem(part: str) -> bool:
    for length, digest in STEMS:
        for start in range(len(part) - length + 1):
            if hashlib.sha256(part[start:start + length].encode("utf-8")).hexdigest() == digest:
                return True
    return False


def _names(token: str) -> bool:
    if _retired(token):
        return True
    parts = set(_COARSE.split(token)) | set(_FINE.split(token))
    if any(_retired(part) for part in parts if part):
        return True
    return any(_contains_stem(part) for part in _FINE.split(token) if part)


def names_retired_account(text: str) -> bool:
    """True when ``text`` contains a retired account's identifier."""
    new = _tokens(text) - _clean
    if any(_names(token) for token in new):
        return True
    _clean.update(new)
    return False


def is_retired_login(login: str) -> bool:
    """True when ``login`` is itself one of the retired accounts."""
    return _retired(login.strip().lower())


def _decode(data: bytes) -> str | None:
    if len(data) > _MAX_BYTES or b"\0" in data[:8192]:
        return None
    return data.decode("utf-8", errors="replace")


def scan(files: Iterable[tuple[str, bytes | None]]) -> list[str]:
    """``path:line`` for every line (``path`` for a path itself) naming a retired account."""
    hits: list[str] = []
    for path, data in files:
        if names_retired_account(path):
            hits.append(path)
        text = _decode(data) if data is not None else None
        if text is None or not names_retired_account(text):
            continue
        hits.extend(
            f"{path}:{number}"
            for number, line in enumerate(text.splitlines(), 1)
            if names_retired_account(line)
        )
    return hits


def _git(root: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *arguments], check=True, capture_output=True,
    ).stdout


def checkout_files(root: Path) -> Iterator[tuple[str, bytes | None]]:
    """Tracked files as they are in the working tree of ``root``."""
    for raw in _git(root, "ls-files", "-z").split(b"\0"):
        if not raw:
            continue
        path = raw.decode("utf-8", errors="replace")
        file = root / path
        if file.is_symlink() or not file.is_file():
            yield path, None
            continue
        yield path, file.read_bytes()


def commit_files(root: Path, commit: str) -> Iterator[tuple[str, bytes | None]]:
    """Files of ``commit``'s tree, read from the object store, not a checkout."""
    listing = _git(root, "ls-tree", "-r", "-z", "--full-tree", commit)
    entries = []
    for row in listing.split(b"\0"):
        if not row:
            continue
        meta, _, path = row.partition(b"\t")
        mode, kind, sha = meta.split()
        entries.append((path.decode("utf-8", errors="replace"), kind, sha, mode))
    blobs = [sha for _, kind, sha, mode in entries if kind == b"blob" and mode != b"120000"]
    contents: dict[bytes, bytes] = {}
    if blobs:
        output = subprocess.run(
            ["git", "-C", str(root), "cat-file", "--batch"],
            input=b"\n".join(blobs) + b"\n", check=True, capture_output=True,
        ).stdout
        offset = 0
        while offset < len(output):
            end = output.index(b"\n", offset)
            sha, _, size = output[offset:end].split()
            start = end + 1
            contents[sha] = output[start:start + int(size)]
            offset = start + int(size) + 1
    for path, kind, sha, mode in entries:
        yield path, contents.get(sha) if kind == b"blob" and mode != b"120000" else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--commit", help="scan this commit's tree instead of the checkout")
    source.add_argument("--stdin", action="store_true", help="scan text on standard input")
    parser.add_argument("--root", default=".", help="the repository (default: the current directory)")
    arguments = parser.parse_args(argv)
    root = Path(arguments.root).resolve()
    if arguments.stdin:
        hits = scan([("<stdin>", sys.stdin.buffer.read())])
    elif arguments.commit:
        hits = scan(commit_files(root, arguments.commit))
    else:
        hits = scan(checkout_files(root))
    if hits:
        print("names a retired account:", *hits, sep="\n  ", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

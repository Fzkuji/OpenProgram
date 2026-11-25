"""The code's files as code_origin reads them: the tracked files, their Git blob hashes
(the SWHID of a content is swh:1:cnt:<sha1_git>), vendored third-party folders, the
provenance a file states about itself ("adapted from <url>") and winnowing fingerprints
for near-copies. Plain code, standard library only."""

import hashlib
import os
import re
import subprocess
import zlib
from pathlib import Path

MAX_BYTES = 1_000_000          # larger files are data, not read
TRIVIAL_LINES = 10             # fewer non-blank lines than this, or fewer bytes than TRIVIAL_BYTES:
TRIVIAL_BYTES = 300            # too small to say it was copied (empty __init__.py, a version pin)
HEADER_LINES = 40
SKIP_DIRS = {".git", "drafts", "node_modules", "__pycache__", ".venv", "venv", "env", "site-packages", ".lake",
             ".mypy_cache", ".pytest_cache", ".tox", "build", "dist"}
VENDOR_DIRS = {"third_party", "thirdparty", "third-party", "3rdparty", "vendor", "vendored", "external",
               "externals", "extern", "contrib"}
LICENSE_FILE = re.compile(r"^(?:LICEN[CS]E|COPYING|UNLICENSE)(?:[-._][\w.-]*)?$", re.I)
NOTICE_FILE = re.compile(r"^(?:NOTICE|THIRD[-_ ]?PARTY[-_ ]?NOTICES?|ACKNOWLEDG(?:E)?MENTS?|CREDITS|AUTHORS)(?:\.\w+)?$",
                         re.I)
CODE_EXT = {".py", ".pyx", ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".cu", ".cuh", ".js", ".mjs", ".ts",
            ".jsx", ".tsx", ".java", ".go", ".rs", ".rb", ".jl", ".r", ".m", ".sh", ".lua", ".scala", ".kt",
            ".swift", ".php", ".pl", ".f90", ".f", ".sql", ".lean", ".hs", ".ml", ".cs", ".sty", ".cls", ".bst"}
URL = re.compile(r"https?://[^\s'\"<>()\[\]{}`,;]+")
# A file saying where it came from. Strong phrases count alone; weak ones only with a URL on the line.
STRONG = re.compile(r"\b(?:adapted|copied|taken|borrowed|derived|ported|forked|lifted|vendored|modified|"
                    r"extracted|reproduced)\s+(?:directly\s+|verbatim\s+|heavily\s+|largely\s+)?from\b", re.I)
WEAK = re.compile(r"\b(?:based\s+(?:on|off)|inspired\s+by|credits?\b|source\s*:|original(?:ly)?\b|"
                  r"reference\s*:|see\s*:?)", re.I)
COPYRIGHT = re.compile(r"^\W*(?:copyright\b|\(c\)\s*\d{4}|©)\s*.{3,}$", re.I | re.M)
COMMENT_LINE = re.compile(r"^\s*(?:#|//|/\*|\*|--|%|;|<!--)")


def sha1_git(data: bytes) -> str:
    """Git's blob hash: sha1 of "blob <length>\\0" and the bytes."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def swhid(sha: str) -> str:
    return f"swh:1:cnt:{sha}"


def tracked(code: Path) -> list[str]:
    """The files Git tracks under code (relative to it); every file when code is not a
    Git working tree. Skipped folders and symbolic links are left out."""
    done = subprocess.run(["git", "-C", str(code), "ls-files", "-z"], capture_output=True)
    if done.returncode == 0:
        names = [n for n in done.stdout.decode("utf-8", "replace").split("\0") if n]
    else:
        names = []
        for root, dirs, files in os.walk(code):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
            rel = Path(root).relative_to(code)
            names += [(rel / f).as_posix() for f in sorted(files) if not f.startswith(".")]
    out = []
    for name in names:
        path = code / name
        if set(Path(name).parts[:-1]) & SKIP_DIRS or path.name == ".env" or path.is_symlink() or not path.is_file():
            continue
        out.append(name)
    return sorted(out)


def read(code: Path, names: list[str]) -> tuple[list[dict], list[dict]]:
    """Each readable file with its hash and text; and the skipped ones (binary, over 1 MB)."""
    files, skipped = [], []
    for name in names:
        path = code / name
        size = path.stat().st_size
        if size > MAX_BYTES:
            skipped.append({"file": name, "why": f"{size // 1000} kB, over 1 MB"})
            continue
        data = path.read_bytes()
        if b"\0" in data[:8192]:
            skipped.append({"file": name, "why": "binary"})
            continue
        text = data.decode("utf-8", "replace")
        lines = sum(1 for line in text.splitlines() if line.strip())
        sha = sha1_git(data)
        files.append({"file": name, "size": size, "sha1_git": sha, "swhid": swhid(sha), "text": text,
                      "lines": lines, "trivial": lines < TRIVIAL_LINES or size < TRIVIAL_BYTES,
                      "code": Path(name).suffix.lower() in CODE_EXT})
    return files, skipped


def vendor_roots(names: list[str]) -> list[str]:
    """Folders holding third-party code: a folder named like third_party/vendor/external
    (each child folder is its own root), and any non-root folder with its own LICENSE."""
    roots = set()
    for name in names:
        parts = Path(name).parts
        for i, part in enumerate(parts[:-1]):
            if part.lower() in VENDOR_DIRS:
                roots.add("/".join(parts[:i + 2]) if i + 2 < len(parts) else "/".join(parts[:i + 1]))
                break
        if len(parts) > 1 and LICENSE_FILE.match(parts[-1]):
            roots.add("/".join(parts[:-1]))
    return sorted(roots, key=lambda r: (-r.count("/"), r))


def vendor_root(name: str, roots: list[str]) -> str:
    """The deepest vendored root holding name, or ""."""
    for root in roots:
        if name.startswith(root + "/"):
            return root
    return ""


def submodules(code: Path) -> dict[str, str]:
    """.gitmodules: path -> URL."""
    path = code / ".gitmodules"
    if not path.is_file():
        return {}
    out, current = {}, {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"\s*(path|url)\s*=\s*(.+?)\s*$", line)
        if line.strip().startswith("["):
            current = {}
        elif m:
            current[m.group(1)] = m.group(2)
            if "path" in current and "url" in current:
                out[current["path"]] = current["url"]
    return out


def provenance(text: str) -> list[dict]:
    """Lines where the file says it came from somewhere: a strong phrase ("adapted from"),
    or a weak one ("based on", "source:") with a URL on that line. The URL may sit on the
    next two lines."""
    lines = text.splitlines()
    out = []
    for i, line in enumerate(lines):
        strong = STRONG.search(line)
        urls = URL.findall(line)
        if not strong and not (WEAK.search(line) and urls):
            continue
        if strong and not urls:
            urls = [u for nxt in lines[i + 1:i + 3] for u in URL.findall(nxt)]
        out.append({"line": i + 1, "quote": line.strip()[:240], "urls": [clean_url(u) for u in urls],
                    "strong": bool(strong)})
    return out[:10]


def header_urls(text: str) -> list[str]:
    """URLs in the first HEADER_LINES lines."""
    head = "\n".join(text.splitlines()[:HEADER_LINES])
    return list(dict.fromkeys(clean_url(u) for u in URL.findall(head)))


def copyrights(text: str) -> list[str]:
    return list(dict.fromkeys(uncomment(m.group(0))[:200] for m in COPYRIGHT.finditer(text)))[:5]


def uncomment(line: str) -> str:
    """A line without its leading comment marker ("# ", "// ", " * ")."""
    return re.sub(r"^\s*(?:#+|//+|/?\*+|--|%+|;+)\s*", "", line).strip()


def clean_url(url: str) -> str:
    return url.rstrip(".:!?'\"")


def header(text: str, limit: int = HEADER_LINES) -> str:
    return "\n".join(f"{i:4d}| {line}" for i, line in enumerate(text.splitlines()[:limit], 1))


# Repository URLs ----------------------------------------------------------------------------

FORGE = re.compile(r"^https?://(?:www\.)?(github\.com|gitlab\.com|bitbucket\.org|codeberg\.org)/([^/#?]+)/([^/#?]+)"
                   r"(?:/(?:-/)?(?:blob|tree|raw|src)/([^/#?]+)/([^#?]*))?", re.I)
RAW = re.compile(r"^https?://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/([^#?]+)", re.I)


def repository(url: str) -> tuple[str, str]:
    """(origin URL as Software Heritage names it, path inside it) for a forge URL, else ("", "")."""
    url = clean_url(url.strip())
    m = re.match(r"^git@([^:]+):([^/]+)/(.+?)(?:\.git)?$", url)
    if m:
        url = f"https://{m.group(1)}/{m.group(2)}/{m.group(3)}"
    m = RAW.match(url)
    if m:
        return f"https://github.com/{m.group(1)}/{m.group(2)}", m.group(4).strip("/")
    m = FORGE.match(url)
    if not m:
        return "", ""
    repo = re.sub(r"\.git$", "", m.group(3))
    return f"https://{m.group(1).lower()}/{m.group(2)}/{repo}", (m.group(5) or "").strip("/")


def own_origin(code: Path) -> str:
    """The code's own public origin: Git's origin remote when it is a forge URL."""
    done = subprocess.run(["git", "-C", str(code), "remote", "get-url", "origin"], capture_output=True, text=True)
    return repository(done.stdout.strip())[0] if done.returncode == 0 else ""


# Winnowing (Schleimer, Wilkerson and Aiken 2003; the fingerprinting of MOSS) ----------------

K = 30          # characters per k-gram after normalisation
W = 30          # window: any shared run of K + W - 1 characters is found
ADAPTED = 0.5   # share of the smaller file's fingerprints found in the other
MIN_SHARED = 15
MAX_WINNOW = 300_000


def normalise(text: str) -> str:
    """Comment-only lines dropped (a shared license header is not shared code), whitespace
    removed, lower case."""
    kept = [line for line in text.splitlines() if not COMMENT_LINE.match(line)]
    return re.sub(r"\s+", "", "\n".join(kept)).lower()


def fingerprints(text: str, k: int = K, w: int = W) -> set[int]:
    norm = normalise(text[:MAX_WINNOW])
    if len(norm) < k:
        return set()
    hashes = [zlib.crc32(norm[i:i + k].encode()) for i in range(len(norm) - k + 1)]
    if len(hashes) <= w:
        return {min(hashes)}
    out, last = set(), -1
    for i in range(len(hashes) - w + 1):
        window = hashes[i:i + w]
        low = min(window)
        j = i + w - 1 - window[::-1].index(low)        # rightmost minimum
        if j != last:
            out.add(low)
            last = j
    return out


def similarity(a: set[int], b: set[int]) -> tuple[float, int]:
    """(shared / fingerprints of the smaller, shared)."""
    if not a or not b:
        return 0.0, 0
    shared = len(a & b)
    return shared / min(len(a), len(b)), shared


__all__ = ["fingerprints", "provenance", "read", "repository", "sha1_git", "similarity", "tracked", "vendor_roots"]

"""Get the full text of one work: the arXiv LaTeX source when there is one, else a PDF
read by pdftotext. Plain code, no model calls.

`work` is an arXiv id (or abs/pdf URL), a DOI (or doi.org URL) or the path of a PDF. A
DOI is turned into an arXiv id through Semantic Scholar, then OpenAlex; when neither
knows one, their open-access PDF is read. Every JSON GET goes through
citations.sources.fetch; the archive and PDF downloads, which are bytes, go through
fetch_bytes below with the same retries and the same UNAVAILABLE record, so tests
replace those two alone. Archives are read in memory and never extracted to disk.
"""

import gzip
import io
import re
import shutil
import subprocess
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from auto_research.tasks.citations import sources
from auto_research.tasks.survey import search

ARXIV_NEW = re.compile(r"(?:arxiv[:./]\s*|arxiv\.org/(?:abs|pdf|e-print)/)?(\d{4}\.\d{4,5})(?:v\d+)?(?:\.pdf)?$", re.I)
ARXIV_OLD = re.compile(r"(?:arxiv[:.]\s*|arxiv\.org/(?:abs|pdf|e-print)/)?([a-z\-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?$", re.I)
DOI = re.compile(r"(?:https?://(?:dx\.)?doi\.org/|doi:\s*)?(10\.\d{4,9}/\S+)$", re.I)
KEEP = (".tex", ".bbl", ".bib", ".ltx")
MAX_FILE = 4_000_000          # bytes of one source file read from the archive
MAX_BYTES = 60_000_000        # bytes of one download
PDF_TIMEOUT = 180
INPUT = re.compile(r"\\(?:input|include|subfile)\s*\{([^}]+)\}")


def parse_work(work: str) -> tuple[str, str]:
    """(kind, value): ("arxiv", id), ("doi", doi) or ("pdf", path). ValueError otherwise."""
    w = str(work or "").strip().rstrip("/")
    if not w:
        raise ValueError("work is empty: give an arXiv id, a DOI or a PDF path")
    path = Path(w).expanduser()
    if w.lower().endswith(".pdf") and path.is_file():
        return "pdf", str(path.resolve())
    m = DOI.search(w)
    if m and w.lower().startswith(("10.", "doi:", "http://doi", "https://doi", "http://dx", "https://dx")):
        doi = m.group(1).lower()
        if doi.startswith("10.48550/arxiv."):
            return "arxiv", doi.split("arxiv.", 1)[1]
        return "doi", doi
    for pattern in (ARXIV_NEW, ARXIV_OLD):
        m = pattern.search(w)
        if m and (m.start() == 0 or "arxiv" in w.lower()):
            return "arxiv", m.group(1)
    if w.lower().endswith(".pdf"):
        raise ValueError(f"the PDF {path} does not exist")
    raise ValueError(f"work is not an arXiv id, a DOI or a PDF path: {w}")


def fetch_bytes(url: str, label: str = "", tries: int = 3) -> bytes | None:
    """A GET returning bytes, with the retries and failure record of sources.fetch."""
    for attempt in range(tries):
        try:
            request = urllib.request.Request(url, headers=sources.UA)
            with urllib.request.urlopen(request, timeout=sources.TIMEOUT * 3) as r:
                data = r.read(MAX_BYTES + 1)
            return data[:MAX_BYTES]
        except urllib.error.HTTPError as e:
            if e.code in (400, 403, 404):
                break
        except Exception:  # noqa: S110  a timeout is retried below
            pass
        time.sleep(1.5 * (attempt + 1))
    if label:
        sources.UNAVAILABLE.add(label)
    return None


def doi_target(doi: str) -> tuple[str, str]:
    """("arxiv", id) or ("pdf_url", url) for a DOI, from Semantic Scholar then OpenAlex; ("", "") when neither knows."""
    d = sources.fetch(f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}",
                      {"fields": "externalIds,openAccessPdf,title"}, label="Semantic Scholar",
                      headers=sources._env("S2_API_KEY", "x-api-key")) or {}
    arxiv = (d.get("externalIds") or {}).get("ArXiv") or ""
    if arxiv:
        return "arxiv", arxiv
    pdf = (d.get("openAccessPdf") or {}).get("url") or ""
    w = sources.fetch(f"https://api.openalex.org/works/doi:{doi}",
                      {"select": "id,locations,best_oa_location", **sources._env("OPENALEX_API_KEY", "api_key")},
                      label="OpenAlex") or {}
    for loc in w.get("locations") or []:
        m = search.ARXIV_ID.search(loc.get("landing_page_url") or "") or search.ARXIV_ID.search(loc.get("pdf_url") or "")
        if m:
            return "arxiv", m.group(1)
    pdf = pdf or (w.get("best_oa_location") or {}).get("pdf_url") or ""
    return ("pdf_url", pdf) if pdf else ("", "")


def decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def unpack(data: bytes) -> tuple[dict[str, str], bytes | None]:
    """An arXiv e-print as (source files by name, PDF bytes when the e-print is a PDF)."""
    raw = data
    if raw[:2] == b"\x1f\x8b":
        try:
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
                return members(tar), None
        except tarfile.TarError:
            try:
                raw = gzip.decompress(raw)
            except (OSError, EOFError):
                return {}, None
    if raw[:5] == b"%PDF-":
        return {}, raw
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tar:
            return members(tar), None
    except tarfile.TarError:
        pass
    text = decode(raw)
    return ({"main.tex": text}, None) if "\\begin{document}" in text or "\\section" in text else ({}, None)


def members(tar: tarfile.TarFile) -> dict[str, str]:
    files = {}
    for m in tar.getmembers():
        name = m.name.lstrip("./")
        if not m.isfile() or m.size > MAX_FILE or not name.lower().endswith(KEEP) or ".." in Path(name).parts:
            continue
        f = tar.extractfile(m)
        if f is not None:
            files[name] = decode(f.read())
    return files


MIN_PROSE = 40      # words of prose below which a LaTeX source is not the work itself


def prose_words(tex: str) -> int:
    """Words of the document body outside commands, math and comments."""
    body = tex.split("\\begin{document}", 1)[-1]
    body = re.sub(r"(?<!\\)%.*", "", body)
    body = re.sub(r"\$[^$]*\$|\\[a-zA-Z@]+\*?(\[[^\]]*\])?(\{[^{}]*\})?", " ", body)
    return len(re.findall(r"[A-Za-z]{2,}", body))


def included_pdf(data: bytes, tex: str) -> bytes | None:
    """The PDF a wrapper source includes with \\includepdf, read from the e-print archive."""
    m = re.search(r"\\includepdf(?:\[[^\]]*\])?\{([^}]+)\}", tex)
    if not m or data[:2] != b"\x1f\x8b":
        return None
    want = m.group(1).strip()
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            for member in tar.getmembers():
                name = member.name.lstrip("./")
                if member.isfile() and name in (want, want + ".pdf") and member.size <= 50 * MAX_FILE:
                    f = tar.extractfile(member)
                    raw = f.read() if f else b""
                    return raw if raw[:5] == b"%PDF-" else None
    except tarfile.TarError:
        return None
    return None


def strip_comments(text: str) -> str:
    return re.sub(r"(?<!\\)%.*", "", text)


def main_file(files: dict[str, str]) -> str:
    """The file holding \\documentclass and \\begin{document}; main.tex or ms.tex first, else the largest."""
    mains = [n for n, t in files.items() if n.endswith((".tex", ".ltx"))
             and re.search(r"^\s*\\documentclass", strip_comments(t), re.M) and "\\begin{document}" in t]
    if not mains:
        mains = [n for n, t in files.items() if n.endswith((".tex", ".ltx")) and "\\begin{document}" in t]
    if not mains:
        return ""
    return min(mains, key=lambda n: (Path(n).stem not in ("main", "ms", "paper"), -len(files[n])))


def flatten(files: dict[str, str], name: str, depth: int = 0) -> str:
    """The main file with every \\input and \\include inlined from the archive's files."""
    if depth > 20:
        return ""
    base = str(Path(name).parent)

    def find(target: str) -> str | None:
        target = target.strip()
        for cand in (target, target + ".tex", str(Path(base) / target), str(Path(base) / (target + ".tex"))):
            cand = cand.lstrip("./")
            if cand in files:
                return cand
        return None

    def inline(m):
        hit = find(m.group(1))
        return flatten(files, hit, depth + 1) if hit else ""
    return INPUT.sub(inline, strip_comments(files.get(name, "")))


def pdf_text(path: Path) -> str:
    """The text of a PDF by pdftotext, pages separated by form feeds."""
    if not shutil.which("pdftotext"):
        raise ValueError("pdftotext is not installed (poppler)")
    try:
        done = subprocess.run(["pdftotext", "-enc", "UTF-8", str(path), "-"], capture_output=True, text=True,
                              timeout=PDF_TIMEOUT)
    except subprocess.TimeoutExpired as e:
        raise ValueError("pdftotext timed out") from e
    if done.returncode != 0:
        raise ValueError(f"pdftotext exited {done.returncode}: {' '.join(done.stderr.split())[-200:]}")
    return done.stdout


def get_work(work: str, out: Path, notes: list[str]) -> dict:
    """The work's text: {kind: "latex", files, main, tex, id, url} or {kind: "pdf", text, id, url},
    or {kind: ""} when nothing could be read. Downloaded PDFs are saved in out as source.pdf."""
    kind, value = parse_work(work)
    if kind == "pdf":
        return {"kind": "pdf", "text": pdf_text(Path(value)), "id": value, "url": ""}
    if kind == "doi":
        how, target = doi_target(value)
        if how == "arxiv":
            kind, value = "arxiv", re.sub(r"v\d+$", "", target)
        elif how == "pdf_url":
            return read_pdf_url(target, value, out, notes, "open-access PDF")
        else:
            notes.append(f"DOI {value}: neither Semantic Scholar nor OpenAlex gave an arXiv id or an open-access PDF")
            return {"kind": "", "id": value, "url": f"https://doi.org/{value}"}
    url = f"https://arxiv.org/abs/{value}"
    data = fetch_bytes(f"https://arxiv.org/e-print/{value}", label="arXiv source")
    pdf = None
    if data:
        files, pdf = unpack(data)
        main = main_file(files)
        if main:
            tex = flatten(files, main)
            if prose_words(tex) >= MIN_PROSE and not re.search(r"\\includepdf\b", tex):
                return {"kind": "latex", "files": files, "main": main, "tex": tex, "id": value, "url": url}
            # a source that only wraps the PDF (\includepdf) or holds no prose: read the PDF instead
            pdf = included_pdf(data, tex)
            notes.append(f"arXiv {value}: the LaTeX source holds no text of its own (it includes a PDF); "
                         "the PDF is read instead")
        if pdf is None:
            notes.append(f"arXiv {value}: the source has no LaTeX main file; the PDF is read instead")
    else:
        notes.append(f"arXiv {value}: the LaTeX source could not be downloaded; the PDF is read instead")
    if pdf is not None:
        return save_pdf(pdf, value, url, out)
    return read_pdf_url(f"https://arxiv.org/pdf/{value}", value, out, notes, "arXiv PDF", url)


def read_pdf_url(pdf_url: str, wid: str, out: Path, notes: list[str], label: str, url: str = "") -> dict:
    data = fetch_bytes(pdf_url, label=label)
    if not data or data[:5] != b"%PDF-":
        notes.append(f"{wid}: the PDF at {pdf_url} could not be downloaded")
        return {"kind": "", "id": wid, "url": url or pdf_url}
    return save_pdf(data, wid, url or pdf_url, out)


def save_pdf(data: bytes, wid: str, url: str, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    path = out / "source.pdf"
    path.write_bytes(data)
    return {"kind": "pdf", "text": pdf_text(path), "id": wid, "url": url}


__all__ = ["fetch_bytes", "flatten", "get_work", "main_file", "parse_work", "pdf_text", "unpack"]

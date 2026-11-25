"""Read interview data and cut it into codable segments. Plain code, no model calls.

A segment is one speaker turn, or one paragraph when the file has no speaker labels,
with an id `<file>:<line>` (the line, or the paragraph of a .docx, where it starts).
Interviewer turns are kept as the question that precedes the next answer but are not
coded. Segments shorter than MIN_WORDS words ("Yeah.", "Okay") are not coded either.
"""

import re
import zipfile
from pathlib import Path

from ..discovery.notes import read_docx

SUFFIXES = (".txt", ".md", ".docx", ".vtt", ".srt")
MIN_WORDS = 5
# Files in a data folder that document the data rather than hold it.
DOCUMENTATION = {"readme", "license", "licence", "source", "sources", "notice", "citation"}
ASKED_CHARS = 400
# "Interviewer:", "**P3:**", "[00:01:02] Speaker 1:" — a short label, then a colon.
LABEL = re.compile(r"^\s*(?:\[?\(?\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?\)?\]?\s*)?[*_]*"
                   r"(?P<speaker>[A-Za-z][\w .'()\-]{0,38}?)[*_]*\s*:[*_]*\s+(?P<text>\S.*)$")
INTERVIEWER = re.compile(r"\b(interviewer|moderator|facilitator|researcher|host)\b|^(i|q|int|r)$", re.I)
TIMING = re.compile(r"-->")
VOICE = re.compile(r"<v(?:\.[\w.]+)?\s+([^>]+)>")
TAG = re.compile(r"</?[^>]+>")


def collect(paper: Path, data: str) -> tuple[list[Path], list[str]]:
    """The data files named by `data` (comma-separated files or folders inside the
    project), in order, and the files skipped for their type."""
    files, skipped = [], []
    names = [d.strip() for d in str(data).split(",") if d.strip()]
    if not names:
        raise ValueError("data names no file or folder")
    for name in names:
        path = (paper / name).expanduser().resolve()
        try:
            path.relative_to(paper)
        except ValueError:
            raise ValueError(f"{name} is not inside the project") from None
        if not path.exists():
            raise ValueError(f"{name} does not exist")
        found = sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path]
        for p in found:
            rel = p.relative_to(paper)
            if any(part.startswith(".") for part in rel.parts):
                continue
            if p.suffix.lower() in SUFFIXES and (path.is_file() or p.stem.lower() not in DOCUMENTATION):
                if p not in files:
                    files.append(p)
            else:
                skipped.append(str(rel))
    return files, skipped


def lines_of(path: Path) -> list[tuple[int, str]]:
    """(line number, text) of a file; for .docx the paragraph number."""
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return [(k, t) for _, k, t in read_docx(path)]
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    return list(enumerate(text.splitlines(), 1))


def turn_based(lines: list[tuple[int, str]], docx: bool = False) -> bool:
    """A file is a list of turns when most of its paragraphs start with a speaker label
    and at least two speakers occur."""
    starts, labelled, speakers = 0, 0, set()
    previous_blank = True
    for _, t in lines:
        if not t.strip():
            previous_blank = True
            continue
        m = LABEL.match(t)
        if docx or previous_blank or m:
            starts += 1
            if m:
                labelled += 1
                speakers.add(m.group("speaker").strip().casefold())
        previous_blank = False
    return starts > 0 and labelled >= 0.5 * starts and len(speakers) >= 2


def turns(lines: list[tuple[int, str]], docx: bool) -> list[dict]:
    """Group lines into turns ({line, speaker, text}); unlabelled lines continue a turn."""
    out = []
    for n, t in lines:
        if not t.strip():
            continue
        m = LABEL.match(t)
        if m:
            out.append({"line": n, "speaker": m.group("speaker").strip(), "text": m.group("text").strip()})
        elif out:
            out[-1]["text"] += "\n" + t.strip()
        else:
            out.append({"line": n, "speaker": "", "text": t.strip()})
    return out


def paragraphs(lines: list[tuple[int, str]], docx: bool) -> list[dict]:
    """Paragraphs: blank-line separated in text, one per paragraph in .docx."""
    if docx:
        return [{"line": n, "speaker": "", "text": t.strip()} for n, t in lines if t.strip()]
    out, cur = [], None
    for n, t in lines:
        if not t.strip():
            cur = None
            continue
        if t.lstrip().startswith("#") and cur is None:      # a markdown heading is not data
            continue
        if cur is None:
            cur = {"line": n, "speaker": "", "text": t.strip()}
            out.append(cur)
        else:
            cur["text"] += "\n" + t.strip()
    return out


def cues(path: Path) -> list[dict]:
    """Subtitle cues (.vtt or .srt) as turns: consecutive cues of one speaker are merged;
    without speakers, cues are merged up to a sentence end."""
    raw = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    out, block = [], []

    def close(block):
        timing = next((i for i, (_, t) in enumerate(block) if TIMING.search(t)), None)
        if timing is None:
            return
        body = [(n, t) for n, t in block[timing + 1:] if t.strip()]
        if not body:
            return
        joined = " ".join(t.strip() for _, t in body)
        voice = VOICE.search(joined)
        speaker = voice.group(1).strip() if voice else ""
        text = " ".join(TAG.sub("", joined).split())
        if not speaker:
            m = LABEL.match(text)
            if m:
                speaker, text = m.group("speaker").strip(), m.group("text").strip()
        if not text:
            return
        last = out[-1] if out else None
        if last and speaker and last["speaker"] == speaker:
            last["text"] += " " + text
        elif last and not speaker and not last["speaker"] and not re.search(r"[.!?…]\"?$", last["text"]):
            last["text"] += " " + text
        else:
            out.append({"line": body[0][0], "speaker": speaker, "text": text})

    for n, t in enumerate(raw, 1):
        if not t.strip():
            close(block)
            block = []
        elif not (n == 1 and t.startswith("WEBVTT")):
            block.append((n, t))
    close(block)
    return out


def read_file(paper: Path, path: Path) -> list[dict]:
    """The segments of one file: id, file, line, speaker, participant, text, asked
    (the interviewer turn before it) and code (whether it is coded)."""
    rel = str(path.relative_to(paper))
    name = path.name
    suffix = path.suffix.lower()
    if suffix in (".vtt", ".srt"):
        units = cues(path)
    else:
        lines = lines_of(path)
        units = turns(lines, suffix == ".docx") if turn_based(lines, suffix == ".docx") else paragraphs(lines, suffix == ".docx")
    speakers = {u["speaker"] for u in units if u["speaker"] and not INTERVIEWER.search(u["speaker"])}
    stem = path.stem
    out, asked, seen = [], "", set()
    for u in units:
        interviewer = bool(u["speaker"]) and bool(INTERVIEWER.search(u["speaker"]))
        if interviewer:
            asked = " ".join(u["text"].split())[:ASKED_CHARS]
            continue
        sid = f"{name}:{u['line']}"
        if sid in seen:
            continue
        seen.add(sid)
        participant = f"{stem}/{u['speaker']}" if len(speakers) > 1 and u["speaker"] else stem
        out.append({"id": sid, "file": rel, "line": u["line"], "speaker": u["speaker"], "participant": participant,
                    "text": u["text"], "asked": asked, "code": len(u["text"].split()) >= MIN_WORDS})
        asked = ""
    return out


def read_all(paper: Path, files: list[Path], notes: list[str]) -> list[dict]:
    segments, ids = [], set()
    for path in files:
        try:
            got = read_file(paper, path)
        except (OSError, zipfile.BadZipFile, ValueError) as e:
            notes.append(f"{path.relative_to(paper)} could not be read: {e}")
            continue
        if not got:
            notes.append(f"{path.relative_to(paper)} has no text")
        for s in got:
            if s["id"] in ids:                    # two files of one name in different folders
                s["id"] = f"{s['file']}:{s['line']}"
            ids.add(s["id"])
            segments.append(s)
    return segments


# Verbatim quotes: compared with case, whitespace, curly quotes and dashes folded.
FOLD = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})
ELLIPSIS = re.compile(r"\s*(?:\.\.\.|…|\[\.\.\.\]|\[…\])\s*")
MIN_QUOTE_WORDS = 3


def fold(text: str) -> str:
    return " ".join(str(text).translate(FOLD).split()).casefold()


def quote_in(quote: str, text: str) -> bool:
    """Whether a quote (parts joined by an ellipsis allowed, in order) is in the text."""
    q = str(quote).strip().strip('"“”\'').strip()
    parts = [fold(p) for p in ELLIPSIS.split(q) if p.strip()]
    if not parts or sum(len(p.split()) for p in parts) < MIN_QUOTE_WORDS:
        return False
    hay, at = fold(text), 0
    for p in parts:
        found = hay.find(p, at)
        if found < 0:
            return False
        at = found + len(p)
    return True


__all__ = ["MIN_WORDS", "SUFFIXES", "collect", "quote_in", "read_all", "read_file"]

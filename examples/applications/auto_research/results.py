"""What a task run produced and what the author does with it. Plain code, no model calls.

A run of a task writes a folder under drafts/<workflow>/<time>/: report.md (its verdict
on the line that starts with 结论：), the files it made, and two optional proposals:

- edits.json: changes to the project's files, each {id, file, section, kind, problem,
  quote, replacement}; kind "create" writes a file that does not exist yet.
- questions.json: what only the author can decide, {questions: [{id, title, detail,
  options: [{id, label}]}]}; the task's answer handler (ANSWERS) applies the choice.

Some made files have a place in the project (PLACES): the author puts them there with one
action. Every change made through these actions is logged (LOG) with the task it came
from, so the list of uncommitted changes says where each change came from; committing
or reverting a file clears its line. What the author decided about each run (edits
applied or rejected, answers, the run set aside) is kept in STORE.
"""

import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from . import registry, stages

STORE = Path("drafts") / "auto_research" / "results.json"
LOG = Path("drafts") / "auto_research" / "changes.json"

# Files of a run folder that are its record, not something the author uses.
RECORD = {"run.json", "run.md", "report.md", "edits.json", "questions.json"}
# Folders of a run that hold its working material.
WORKING = {"logs", "scratch", "backup", "cache", "params", "__pycache__"}
BUILD_JUNK = (".aux", ".log", ".fls", ".fdb_latexmk", ".out", ".blg", ".synctex.gz", ".bbl", ".toc", ".DS_Store")

# Where a made file belongs in the project: task -> {file in the run folder: project path}.
PLACES = {
    "publication_list": {"publications.html": "docs/publications.html", "publications.bib": "docs/publications.bib"},
    "software_citation": {"CITATION.cff": "CITATION.cff", "codemeta.json": "codemeta.json"},
    "method_diagram": {"diagram.pdf": "figures/method_diagram.pdf"},
    "paper_statements": {"statements.tex": "statements.tex"},
    "code_wiki": {"code_wiki.md": "docs/code_wiki.md"},
}

KINDS = {".html": "page", ".htm": "page", ".pdf": "pdf", ".png": "image", ".jpg": "image", ".jpeg": "image",
         ".svg": "image", ".md": "text", ".txt": "text", ".tex": "latex", ".bib": "bib", ".csv": "table",
         ".tsv": "table", ".json": "data", ".cff": "text", ".lean": "code", ".py": "code"}

STATUS = re.compile(r"^\**(PASS_WITH_NOTES|PASS|FAIL|BLOCKED|ERROR|PARTIAL|SKIPPED|NEEDS_[A-Z_]+|[A-Z][A-Z_]{2,})\**[\s　：:，,]*")


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default
    except ValueError:
        return default


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _git(paper: Path, *args: str, check: bool = True) -> str:
    done = subprocess.run(["git", "-C", str(paper), *args], capture_output=True)
    if check and done.returncode:
        raise ValueError(done.stderr.decode("utf-8", "replace").strip() or f"git {args[0]} failed")
    return done.stdout.decode("utf-8", "replace")


def _inside(paper: Path, rel: str) -> Path:
    path = (paper / rel).resolve()
    if path == paper or not path.is_relative_to(paper) or ".git" in path.relative_to(paper).parts:
        raise ValueError(f"{rel} is not a file of the project")
    return path


def _run_folder(paper: Path, run: str) -> Path:
    path = _inside(paper, run)
    if not path.is_dir() or path.relative_to(paper).parts[0] != "drafts":
        raise ValueError(f"{run} is not a run folder")
    return path


def _log(paper: Path, paths: list[str], by: str) -> None:
    log = _read(paper / LOG, {})
    for rel in paths:
        log[rel] = {"by": by, "at": datetime.now().isoformat(timespec="seconds")}
    _write(paper / LOG, log)


def _forget(paper: Path, paths: list[str]) -> None:
    log = _read(paper / LOG, {})
    for rel in paths:
        log.pop(rel, None)
    _write(paper / LOG, log)


def _title(task: str) -> str:
    spec = registry.BY_NAME.get(task)
    return spec.title if spec else task


def verdict(report: Path) -> str:
    """The report's conclusion line without its status word."""
    if not report.is_file():
        return ""
    for line in report.read_text(encoding="utf-8").splitlines():
        if line.startswith("结论："):
            return STATUS.sub("", line[len("结论："):].strip()).strip()
    return ""


def status_of(report: Path) -> str:
    """The status word of the report's conclusion line, or ""."""
    if not report.is_file():
        return ""
    for line in report.read_text(encoding="utf-8").splitlines():
        if line.startswith("结论："):
            m = STATUS.match(line[len("结论："):].strip())
            return m.group(1) if m else ""
    return ""


def latest(paper: Path) -> dict:
    """task -> its newest recorded run that wrote a report."""
    out = {}
    for entry in stages.load_state(paper).values():
        task, report = entry.get("task"), entry.get("report")
        if not task or not report:
            continue
        if task not in out or entry.get("at", "") > out[task].get("at", ""):
            out[task] = entry
    return out


def _made(paper: Path, folder: Path, task: str) -> list[dict]:
    places = PLACES.get(task, {})
    out = []
    for path in sorted(folder.iterdir(), key=lambda p: (p.is_dir(), p.name)):
        name = path.name
        if name in RECORD or name.startswith(".") or name.endswith(BUILD_JUNK):
            continue
        if path.is_dir():
            if name in WORKING:
                continue
            out.append({"name": name, "path": path.relative_to(paper).as_posix(), "kind": "folder", "place": "", "placed": False})
            continue
        kind = KINDS.get(path.suffix.lower(), "file")
        if kind == "data" and name not in places:
            continue
        dest = places.get(name, "")
        placed = bool(dest) and (paper / dest).is_file() and (paper / dest).read_bytes() == path.read_bytes()
        out.append({"name": name, "path": path.relative_to(paper).as_posix(), "kind": kind, "place": dest, "placed": placed})
    return out


def _applies(paper: Path, edit: dict) -> bool:
    """Whether the edit can still be applied to the project as it is now."""
    try:
        path = _inside(paper, edit.get("file") or "")
    except ValueError:
        return False
    if edit.get("kind") == "create":
        return not path.exists()
    quote = edit.get("quote") or ""
    return bool(quote) and path.is_file() and path.read_text(encoding="utf-8", errors="replace").count(quote) == 1


def _edits(paper: Path, folder: Path, decided: dict) -> list[dict]:
    record = _read(folder / "edits.json", {})
    out = []
    for e in record.get("edits") or []:
        if not e.get("id") or not e.get("file") or not isinstance(e.get("replacement"), str):
            continue
        if e.get("kind") != "create" and e.get("quote") == e.get("replacement"):
            continue
        state = decided.get(e["id"]) or ("pending" if _applies(paper, e) else "stale")
        out.append({"id": e["id"], "file": e["file"], "section": e.get("section") or "", "kind": e.get("kind") or "",
                    "problem": e.get("problem") or "", "quote": e.get("quote") or "", "replacement": e["replacement"],
                    "state": state})
    return out


def _questions(folder: Path, answers: dict) -> list[dict]:
    record = _read(folder / "questions.json", {})
    return [{**q, "answer": answers.get(q["id"], "")} for q in record.get("questions") or [] if q.get("id")]


def result(paper: Path, task: str, entry: dict | None = None) -> dict | None:
    """The newest run of the task with what is left to do about it, or None."""
    entry = entry or latest(paper).get(task)
    if not entry:
        return None
    report = (paper / entry["report"]).resolve() if not Path(entry["report"]).is_absolute() else Path(entry["report"])
    folder = report.parent
    if not folder.is_dir() or not folder.is_relative_to(paper):
        return None
    run = folder.relative_to(paper).as_posix()
    record = _read(paper / STORE, {}).get(run, {})
    edits = _edits(paper, folder, record.get("edits", {}))
    questions = _questions(folder, record.get("answers", {}))
    made = _made(paper, folder, task)
    spec = registry.BY_NAME.get(task)
    todo = {
        "edits": sum(e["state"] == "pending" for e in edits),
        "questions": sum(not q["answer"] for q in questions),
        "places": sum(bool(f["place"]) and not f["placed"] for f in made),
        "fix": int(entry.get("status") == "FAIL"),
    }
    return {
        "task": task, "title": spec.title if spec else task, "run": run, "at": entry.get("at", ""),
        "status": entry.get("status", ""), "verdict": verdict(report),
        "report": report.relative_to(paper).as_posix() if report.is_file() else "",
        "edits": edits, "questions": questions, "files": made,
        "set_aside": bool(record.get("set_aside")), "todo": todo,
        "pending": not record.get("set_aside") and any(todo.values()),
    }


def pending(paper: Path) -> list[dict]:
    """Every task whose newest run still asks something of the author, newest first."""
    out = []
    for task, entry in latest(paper).items():
        r = result(paper, task, entry)
        if r and r["pending"]:
            out.append(r)
    return sorted(out, key=lambda r: r["at"], reverse=True)


def history(paper: Path, task: str, limit: int = 12) -> list[dict]:
    """The task's earlier runs, newest first: {run, at, status, verdict}."""
    entry = latest(paper).get(task)
    if not entry:
        return []
    folder = (paper / entry["report"]).resolve().parent.parent
    out = []
    for run in sorted((p for p in folder.iterdir() if p.is_dir()), reverse=True)[:limit + 1]:
        report = run / "report.md"
        meta = _read(run / "run.json", {})
        stamp = meta.get("finished") or meta.get("started") or ""
        if not stamp:
            m = re.match(r"(\d{4})(\d\d)(\d\d)-(\d\d)(\d\d)(\d\d)", run.name)
            stamp = f"{m[1]}-{m[2]}-{m[3]}T{m[4]}:{m[5]}:{m[6]}" if m else ""
        out.append({"run": run.relative_to(paper).as_posix(), "at": stamp, "status": status_of(report),
                    "verdict": verdict(report), "report": report.relative_to(paper).as_posix() if report.is_file() else ""})
    return out[:limit]


def _decide(paper: Path, run: str, key: str, values: dict) -> None:
    store = _read(paper / STORE, {})
    store.setdefault(run, {}).setdefault(key, {}).update(values)
    _write(paper / STORE, store)


def apply(paper: Path, task: str, run: str, ids: list[str]) -> dict:
    """Apply the run's proposed edits the author accepted: a quote found exactly once is
    replaced; a create writes a file that does not exist. {applied, failed} by id."""
    folder = _run_folder(paper, run)
    edits = {e["id"]: e for e in _read(folder / "edits.json", {}).get("edits") or [] if e.get("id")}
    applied, failed, files = [], [], []
    for i in ids:
        e = edits.get(i)
        if not e or not _applies(paper, e):
            failed.append(i)
            continue
        path = _inside(paper, e["file"])
        if e.get("kind") == "create":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(e["replacement"], encoding="utf-8")
        else:
            text = path.read_text(encoding="utf-8")
            path.write_text(text.replace(e["quote"], e["replacement"], 1), encoding="utf-8")
        applied.append(i)
        files.append(path.relative_to(paper).as_posix())
    if applied:
        _decide(paper, run, "edits", {i: "applied" for i in applied})
        _log(paper, sorted(set(files)), _title(task))
    return {"applied": applied, "failed": failed}


def reject(paper: Path, run: str, ids: list[str]) -> dict:
    _run_folder(paper, run)
    _decide(paper, run, "edits", {i: "rejected" for i in ids})
    return {"rejected": ids}


def place(paper: Path, task: str, run: str, name: str) -> dict:
    """Copy a made file to its place in the project. A file there with changes that are
    not committed is not overwritten."""
    folder = _run_folder(paper, run)
    dest = PLACES.get(task, {}).get(name)
    if not dest:
        raise ValueError(f"{name} has no place in the project")
    source = folder / name
    if not source.is_file():
        raise ValueError(f"{name} is not in this run")
    target = _inside(paper, dest)
    if target.exists() and _git(paper, "status", "--porcelain", "--", dest, check=False).strip() \
            and dest not in _read(paper / LOG, {}):
        raise ValueError(f"{dest} has changes that are not committed")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    _log(paper, [dest], _title(task))
    return {"placed": dest}


def set_aside(paper: Path, run: str, value: bool = True) -> dict:
    _run_folder(paper, run)
    store = _read(paper / STORE, {})
    store.setdefault(run, {})["set_aside"] = value
    _write(paper / STORE, store)
    return {"set_aside": value}


def _publication_list(paper: Path, folder: Path, question: str, option: str) -> list[str]:
    from .tasks.publist.workflow import answer as settle
    return settle(paper, folder, question, option)


# task -> handler(paper, run folder, question id, option id) -> list of project files it changed
ANSWERS = {"publication_list": _publication_list}


def answer(paper: Path, task: str, run: str, question: str, option: str) -> dict:
    folder = _run_folder(paper, run)
    asked = {q["id"]: q for q in _read(folder / "questions.json", {}).get("questions") or []}
    q = asked.get(question)
    if not q or option not in {o["id"] for o in q.get("options") or []}:
        raise ValueError("no such question or option")
    handler = ANSWERS.get(task)
    if handler is None:
        raise ValueError(f"{task} takes no answers")
    changed = handler(paper, folder, question, option) or []
    _decide(paper, run, "answers", {question: option})
    if changed:
        _log(paper, changed, _title(task))
    return {"answered": question, "changed": changed}


def _numstat(paper: Path, has_head: bool) -> dict:
    if not has_head:
        return {}
    out = {}
    for line in _git(paper, "diff", "--numstat", "HEAD", check=False).splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            out[parts[2]] = (int(parts[0]) if parts[0].isdigit() else 0, int(parts[1]) if parts[1].isdigit() else 0)
    return out


def changes(paper: Path) -> list[dict]:
    """Uncommitted changes outside drafts/: {path, state (new, modified, deleted),
    added, removed, by (the task that made it, or "")}, newest first."""
    has_head = bool(_git(paper, "rev-parse", "--verify", "-q", "HEAD", check=False).strip())
    stats = _numstat(paper, has_head)
    log = _read(paper / LOG, {})
    out = []
    for entry in _git(paper, "status", "--porcelain", "-uall", "-z", check=False).split("\0"):
        if len(entry) < 4:
            continue
        code, rel = entry[:2], entry[3:]
        if rel.startswith("drafts/") or rel.endswith(BUILD_JUNK):
            continue
        path = paper / rel
        state = "new" if code == "??" or "A" in code else "deleted" if "D" in code else "modified"
        if state == "new" and path.is_file():
            try:
                added, removed = len(path.read_text(encoding="utf-8").splitlines()), 0
            except (UnicodeDecodeError, OSError):
                added, removed = 0, 0
        else:
            added, removed = stats.get(rel, (0, 0))
        mtime = path.stat().st_mtime if path.exists() else 0
        out.append({"path": rel, "state": state, "added": added, "removed": removed,
                    "by": (log.get(rel) or {}).get("by", ""), "mtime": mtime})
    return sorted(out, key=lambda c: c["mtime"], reverse=True)


def diff(paper: Path, rel: str) -> str:
    """The file's uncommitted change as a unified diff; a new file is all additions."""
    path = _inside(paper, rel)
    tracked = bool(_git(paper, "ls-files", "--", rel, check=False).strip())
    if tracked:
        return _git(paper, "diff", "HEAD", "--", rel, check=False)
    if not path.is_file():
        return ""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return ""
    return f"--- /dev/null\n+++ b/{rel}\n@@ -0,0 +1,{len(lines)} @@\n" + "".join(f"+{l}\n" for l in lines)


def revert(paper: Path, paths: list[str]) -> dict:
    """Undo the uncommitted change of each file: a tracked file returns to its committed
    text, a new file is removed."""
    done = []
    for rel in paths:
        path = _inside(paper, rel)
        tracked = bool(_git(paper, "ls-files", "--", rel, check=False).strip())
        if tracked:
            _git(paper, "checkout", "HEAD", "--", rel)
        elif path.is_file():
            _git(paper, "rm", "--cached", "-q", "--", rel, check=False)
            path.unlink()
        done.append(rel)
    _forget(paper, done)
    return {"reverted": done}


def message(paper: Path, paths: list[str]) -> str:
    """A commit message from where the changes came from."""
    log = _read(paper / LOG, {})
    by: dict[str, list[str]] = {}
    for rel in paths:
        by.setdefault((log.get(rel) or {}).get("by", ""), []).append(rel)
    parts = [f"{who}: {', '.join(files[:4])}{' …' if len(files) > 4 else ''}" for who, files in by.items() if who]
    rest = by.get("", [])
    if rest:
        parts.append(", ".join(rest[:4]) + (" …" if len(rest) > 4 else ""))
    return "Update " + "; ".join(parts) if parts else "Update the project"


def commit(paper: Path, paths: list[str], text: str = "") -> dict:
    """Commit exactly these files (new ones included) with the author's message or one
    written from where the changes came from."""
    if not paths:
        raise ValueError("nothing to commit")
    for rel in paths:
        _inside(paper, rel)
    text = text.strip() or message(paper, paths)
    _git(paper, "add", "-A", "--", *paths)
    _git(paper, "commit", "-q", "-m", text, "--", *paths)
    _forget(paper, paths)
    return {"commit": _git(paper, "rev-parse", "--short", "HEAD").strip(), "message": text}


__all__ = ["ANSWERS", "PLACES", "answer", "apply", "changes", "commit", "diff", "history", "latest", "message",
           "pending", "place", "reject", "result", "revert", "set_aside", "verdict"]

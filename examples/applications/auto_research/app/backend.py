"""Backend of the Auto Research app.

Each operation reads the project or runs one auto_research task's check on it. The work
itself happens in the conversation the app shows for each task: the author's own agent,
working in the project folder, told what the task delivers and what its last check found.
This backend lists and reads what the author looks at there: the task's files, what the
last check wrote, the paper's PDF and what changed since the last commit.
"""

import base64
import json
import os
import subprocess
from pathlib import Path

# The worker starts from launchd with a short PATH; the paper check needs TeX and poppler.
for folder in ("/Library/TeX/texbin", "/opt/homebrew/bin", "/usr/local/bin"):
    if Path(folder).is_dir() and folder not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")


def pdf_library():
    try:
        import pymupdf
    except ImportError:
        import fitz as pymupdf
    return pymupdf

def workflows():
    """auto_research and its libraries, loaded the way `openprogram programs run` loads them."""
    from openprogram.programs._host.packages import load_user_packages
    load_user_packages()
    from auto_research import registry, stages, workflow
    from auto_research import trail as workflow_trail
    from auto_research.tasks.revision.rules import checklist
    from auto_research.tasks.revision.rules import paper as paper_rules
    return registry, stages, workflow, workflow_trail, checklist, paper_rules


def project(context, value=None) -> Path:
    """The project folder: the one the page picked, else the one the host bound the app to."""
    path = (value or {}).get("project") or getattr(context, "project_path", "") or ""
    if not path:
        raise ValueError("choose a project folder first")
    paper = Path(path).expanduser().resolve()
    if not (paper / ".git").exists():
        raise ValueError(f"{paper} is not a Git repository; choose the paper's repository")
    return paper


def projects(value, context):
    """Folders the page can offer: OpenProgram's projects that are Git repositories,
    and the folder the host bound this app to."""
    found = []
    try:
        from openprogram.state.store.project.project_store import list_projects
        for p in list_projects():
            path = Path(p.path)
            if (path / ".git").exists():
                found.append({"name": p.name, "path": str(path)})
    except Exception:  # noqa: S110  an unreadable folder is left out of the list
        pass
    bound = getattr(context, "project_path", "") or ""
    return {"home": str(Path.home()), "bound": bound, "bound_is_repo": bool(bound) and (Path(bound) / ".git").exists(),
            "projects": sorted(found, key=lambda p: p["name"].lower())}


def inside(root: Path, path: str) -> Path:
    """An absolute or root-relative path that stays inside root."""
    target = (Path(path) if Path(path).is_absolute() else root / path).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError(f"{path} is outside {root}")
    return target


def browse(value, context):
    """The subfolders of one folder, for the page's folder browser; Git repositories are marked."""
    home = Path.home()
    path = Path(value.get("path") or home).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"{path} is not a folder")
    entries = []
    try:
        children = sorted(path.iterdir(), key=lambda p: p.name.lower())
    except PermissionError:
        children = []
    for child in children:
        try:
            if child.name.startswith(".") or not child.is_dir():
                continue
            entries.append({"name": child.name, "path": str(child), "is_repo": (child / ".git").exists()})
        except OSError:
            continue
    return {"path": str(path), "parent": str(path.parent) if path != path.parent else "", "home": str(home),
            "is_repo": (path / ".git").exists(), "entries": entries[:800]}


def pick_folder(value, context):
    """The macOS folder chooser. Returns the chosen path, or cancelled."""
    script = ['-e', 'activate', '-e', 'POSIX path of (choose folder with prompt "选择项目文件夹")']
    if value.get("start") and Path(value["start"]).is_dir():
        script[-1] = f'POSIX path of (choose folder with prompt "选择项目文件夹" default location POSIX file "{value["start"]}")'
    try:
        done = subprocess.run(["osascript", *script], capture_output=True, text=True, timeout=900)
    except FileNotFoundError:
        raise RuntimeError("this system has no folder chooser; use the folder browser")
    if done.returncode != 0:
        if "-128" in done.stderr:
            return {"cancelled": True}
        raise RuntimeError(done.stderr.strip() or "the folder chooser did not open")
    return {"path": done.stdout.strip().rstrip("/") or "/"}


# Operations.

def overview(value, context):
    """Every task as the app shows it, with what its form offers."""
    registry, stages, wf, _, checklist, paper_rules = workflows()
    paper = project(context, value)
    view = json.loads(wf.auto_research(str(paper)))
    try:
        from auto_research.tasks.revision.steps.prepare import section_titles
        from auto_research.tasks.revision.steps.prepare.source import flatten
        sections = section_titles(flatten(paper, paper_rules.load(paper).main_tex))
    except Exception as e:                          # no paper yet, or it does not parse
        sections, view["sections_error"] = [], f"{type(e).__name__}: {e}"
    git = lambda *a: subprocess.run(["git", "-C", str(paper), *a], capture_output=True, text=True).stdout.strip()
    pdf = paper / Path(paper_rules.load(paper).main_tex).with_suffix(".pdf")
    return {"project": str(paper), "head": git("rev-parse", "--short", "HEAD"),
            "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": [line for line in git("status", "--porcelain").splitlines() if " drafts/" not in line][:40],
            "tasks": view["tasks"], "plans": view["plans"], "created": view["created"],
            "sections": sections, "sections_error": view.get("sections_error"),
            "phases": [{"id": str(i + 1), "title": title} for i, (title, _) in enumerate(checklist.PHASES)],
            "paper_pdf": pdf.relative_to(paper).as_posix() if pdf.is_file() else ""}


def filled(inputs: dict) -> dict:
    """Entry arguments: empty fields keep the entry's defaults."""
    return {k: v for k, v in inputs.items() if v}


def task_inputs(spec, value: dict) -> dict:
    given = value.get("inputs") or {}
    return {k: str(given.get(k) or "") for k in spec.inputs}


def run_task(value, context):
    """Run one task on the project, or on a draft when draft_dir names one; stream its stages."""
    registry, _, wf, trail_lib, _, _ = workflows()
    paper = project(context, value)
    spec = registry.BY_NAME[value["task"]]
    inputs = task_inputs(spec, value)
    if inputs.get("draft_dir"):
        inputs["draft_dir"] = str(inside(paper, inputs["draft_dir"]))
    context.progress({"stage": f"run {spec.name}", "kind": "code", "event": "start"})
    with trail_lib.listen(context.progress):
        result = json.loads(wf.auto_research(str(paper), task=spec.name, **filled(inputs)))
    return result


def pages(value, context):
    """Page images of a PDF in the project, as JPEG, a few pages per call."""
    fitz = pdf_library()
    paper = project(context, value)
    pdf = inside(paper, value["path"])
    start, count = int(value.get("start", 0)), min(int(value.get("count", 2)), 4)
    zoom = min(max(float(value.get("zoom", 1.6)), 0.5), 3.0)
    out = []
    with fitz.open(pdf) as doc:
        total = len(doc)
        for i in range(start, min(start + count, total)):
            pix = doc[i].get_pixmap(matrix=fitz.Matrix(zoom, zoom))
            out.append({"page": i, "width": pix.width, "height": pix.height,
                        "jpeg": base64.b64encode(pix.tobytes("jpeg", jpg_quality=82)).decode()})
    return {"path": str(pdf.relative_to(paper)), "total": total, "pages": out}


def read_text(value, context):
    """A text file in the project, such as a report."""
    paper = project(context, value)
    path = inside(paper, value["path"])
    return {"path": str(path.relative_to(paper)), "text": path.read_text(encoding="utf-8", errors="replace")[:400_000]}


# What the author looks at beside the conversation.

KINDS = {
    "image": (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"),
    "pdf": (".pdf",),
    "table": (".csv", ".tsv"),
    "markdown": (".md",),
    "html": (".html", ".htm"),
    "text": (".tex", ".bib", ".txt", ".json", ".yaml", ".yml", ".py", ".toml", ".sty", ".cls", ".sh", ".r", ".ipynb"),
}
BUILD_JUNK = (".aux", ".log", ".fls", ".fdb_latexmk", ".out", ".blg", ".synctex.gz", ".bbl", ".toc", ".DS_Store")
SKIP_PARTS = {".git", "__pycache__", "node_modules", ".openprogram", ".claude"}


def kind_of(path: Path) -> str:
    name = path.name.lower()
    return next((kind for kind, suffixes in KINDS.items() if name.endswith(suffixes)), "other")


def item(paper: Path, path: Path, group: str) -> dict:
    stat = path.stat()
    return {"path": path.relative_to(paper).as_posix(), "kind": kind_of(path), "group": group,
            "mtime": stat.st_mtime, "size": stat.st_size}


def files_under(path: Path, limit: int) -> list[Path]:
    """A file, or a folder's files newest first, without build products."""
    if path.is_file():
        return [path]
    if not path.is_dir():
        return []
    found = [p for p in path.rglob("*") if p.is_file() and not p.name.endswith(BUILD_JUNK)
             and not SKIP_PARTS & set(p.relative_to(path).parts)]
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)[:limit]


def artifacts(value, context):
    """What one task, or the project, shows beside its conversation, in four groups:
    changed (files changed since the last commit, newest first), paper (the built PDF),
    deliverables (the task's own files under workflow/) and outputs (what its last check
    wrote: the report and the files beside it)."""
    registry, _, wf, _, _, paper_rules = workflows()
    paper = project(context, value)
    name = value.get("task") or ""
    out, seen = [], set()

    def add(path: Path, group: str):
        rel = path.relative_to(paper).as_posix()
        if rel in seen or not path.is_file():
            return
        seen.add(rel)
        out.append(item(paper, path, group))

    status = subprocess.run(["git", "-C", str(paper), "status", "--porcelain", "-uall", "-z"],
                            capture_output=True).stdout.decode("utf-8", "replace").split("\0")
    changed = []
    for entry in status:
        rel = entry[3:]
        if not rel or entry[:2].strip() == "D" or rel.startswith("drafts/") or rel.endswith(BUILD_JUNK):
            continue
        path = paper / rel
        if path.is_file():
            changed.append(path)
    for path in sorted(changed, key=lambda p: p.stat().st_mtime, reverse=True)[:16]:
        add(path, "changed")

    pdf = paper / Path(paper_rules.load(paper).main_tex).with_suffix(".pdf")
    if pdf.is_file():
        add(pdf, "paper")

    if name:
        if name not in registry.BY_NAME:
            raise ValueError(f"no task named {name}")
        view = json.loads(wf.auto_research(str(paper)))
        task = next((t for t in view["tasks"] if t["name"] == name), {})
        for artifact in task.get("artifacts") or []:
            for path in files_under(paper / artifact, 24):
                add(path, "deliverables")
        reports = [r.get("report") for r in task.get("last_runs") or [] if r.get("report")]
        if reports:
            report = inside(paper, reports[0])
            add(report, "outputs")
            for path in files_under(report.parent, 16):
                add(path, "outputs")
    return {"items": out}


def read_file(value, context):
    """An image in the project, as base64, for the page to draw."""
    paper = project(context, value)
    path = inside(paper, value["path"])
    if kind_of(path) != "image":
        raise ValueError(f"{path.name} is not an image")
    if path.stat().st_size > 12 * 1024 * 1024:
        raise ValueError(f"{path.name} is larger than 12 MB")
    mime = {".svg": "image/svg+xml", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(path.suffix.lower(),
                                                                                       f"image/{path.suffix.lower()[1:]}")
    return {"path": path.relative_to(paper).as_posix(), "mime": mime, "data": base64.b64encode(path.read_bytes()).decode()}


operations = {"projects": projects, "browse": browse, "pick_folder": pick_folder, "overview": overview,
              "run_task": run_task, "artifacts": artifacts, "pages": pages, "read_text": read_text,
              "read_file": read_file}


# A runner the host starts ahead imports this module before its operation
# arrives: load the workflows and the PDF library then, not while the author waits.
if os.environ.get("OPENPROGRAM_APPLICATION_WARM"):
    try:
        workflows()
        pdf_library()
    except Exception:  # noqa: BLE001,S110  the operation that needs them reports the error
        pass

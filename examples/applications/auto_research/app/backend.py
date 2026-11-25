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
    from auto_research import brief, ledger, progress
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
            "dirty": [c["path"] for c in results_lib().changes(paper)],
            "tasks": view["tasks"], "plans": view["plans"], "created": view["created"],
            "sections": sections, "sections_error": view.get("sections_error"),
            "phases": [{"id": str(i + 1), "title": title} for i, (title, _) in enumerate(checklist.PHASES)],
            "paper_pdf": pdf.relative_to(paper).as_posix() if pdf.is_file() else "",
            "brief": brief.read(paper), "kinds": [{"id": k, "title": t, "about": a} for k, (t, a) in brief.KINDS.items()],
            "suggestion": suggestion(paper), "progress": progress.view(paper, view["tasks"]),
            "deadline": str(paper_rules.load(paper).notes.get("upload_deadline") or ""),
            "spend": ledger.spend(paper), "preregistration": preregistration(paper),
            "consensus": stance_counts(paper)}


# The brief and the reading of where the project stands.

def suggestion(paper: Path) -> dict:
    """The reader's last suggestion of the paper's kind, kept beside the task results."""
    file = paper / "drafts" / "auto_research" / "kind.json"
    try:
        return json.loads(file.read_text(encoding="utf-8")) if file.is_file() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def suggest_kind(value, context):
    """Have the reader suggest what kind of work the paper is, and keep the suggestion."""
    paper = project(context, value)
    _, _, _, trail_lib, _, _ = workflows()
    from auto_research import progress as reading
    context.progress({"stage": "读论文", "kind": "model", "event": "start"})
    with trail_lib.listen(context.progress):
        found = reading.suggest_kind(paper)
    file = paper / "drafts" / "auto_research" / "kind.json"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(found, ensure_ascii=False, indent=2), encoding="utf-8")
    return found


def set_brief(value, context):
    """Set the paper's kind and/or its claim in workflow/project.md."""
    paper = project(context, value)
    workflows()
    from auto_research import brief as project_brief
    return project_brief.write(paper, kind=value.get("kind"), claim=value.get("claim"))


def scan(value, context):
    """Read where every task of the paper's kind stands, and keep the reading for the author to confirm."""
    paper = project(context, value)
    _, _, _, trail_lib, _, _ = workflows()
    from auto_research import progress as reading
    context.progress({"stage": "盘点", "kind": "model", "event": "start"})
    with trail_lib.listen(context.progress):
        return reading.scan(paper)


def approve(value, context):
    """The author's approval of a task's file: its Approved line set to yes."""
    registry, stages, _, _, _, _ = workflows()
    import re
    paper = project(context, value)
    spec = registry.BY_NAME[value["task"]]
    if not spec.approves:
        raise ValueError(f"{spec.title} has nothing to approve")
    if "{section}" in spec.approves and not value.get("section"):
        raise ValueError("choose the section whose plan card to approve")
    rel = spec.approves.format(section=stages.slug(value.get("section", "")))
    path = stages.folder(paper) / rel
    if not path.is_file():
        raise ValueError(f"workflow/{rel} does not exist yet")
    text = path.read_text(encoding="utf-8")
    line = re.compile(r"^Approved:[ \t]*.*$", re.M)
    if not line.search(text):
        raise ValueError(f"workflow/{rel} has no Approved line")
    path.write_text(line.sub("Approved: yes", text, count=1), encoding="utf-8")
    return {"approved": f"workflow/{rel}"}


def plan_rows(paper: Path) -> list[dict]:
    """The author's own rows of experiment_plan.csv, dropped ones left out."""
    from auto_research import checks
    _, rows = checks.table(checks.stages.read(paper, "experiment_plan.csv", None), checks.PLAN_COLUMNS,
                           "experiment_plan.csv")
    return [r for r in checks.own_rows(rows, "experiment_plan.csv") if r["status"] != "dropped"]


def preregistration(paper: Path) -> dict:
    from auto_research import prereg
    try:
        return prereg.compare(paper, plan_rows(paper))
    except Exception:                                   # no plan yet, or one that does not parse
        return {"locked": False, "changed": [], "exploratory": []}


def stance_counts(paper: Path) -> dict:
    from auto_research import checks
    try:
        return checks.consensus(paper)
    except Exception:                                   # no table yet, or one that does not parse
        return {}


def preregister(value, context):
    """The author locks the experiment plan before the results are in (workflow/preregistration.json)."""
    paper = project(context, value)
    workflows()
    from auto_research import prereg
    rows = plan_rows(paper)
    if not rows:
        raise ValueError("experiment_plan.csv has no planned experiment to lock")
    data = prereg.lock(paper, rows, reason=str(value.get("reason") or ""))
    return {"locked": len(data["rows"]), "at": data["at"]}


def import_library(value, context):
    """Merge an exported library (.bib or CSL-JSON) into the paper's bibliography."""
    paper = project(context, value)
    workflows()
    from auto_research.tasks.library import import_library as merge
    return json.loads(merge(str(paper), str(Path(value["bib_path"]).expanduser()), target=value.get("target", "")))


def apply_edit(value, context):
    """Apply one edit the author accepted (a .tex file or a workflow/ file); only an edit whose quote occurs once applies."""
    paper = project(context, value)
    workflows()
    from auto_research.tasks.proofread import apply_edit as apply
    root = Path(value.get("root") or paper).expanduser().resolve()
    if root != paper and paper not in root.parents:
        raise ValueError("an edit applies only inside the project")
    return {"applied": apply(str(root), value["edit"])}


def import_tracking(value, context):
    """Append runs logged in W&B or MLflow to results.tsv."""
    paper = project(context, value)
    workflows()
    from auto_research.tasks.reproduce import import_tracking as load
    source = str(value["source"])
    if not source.startswith("wandb:"):
        source = str(Path(source).expanduser())
    return json.loads(load(str(paper), source))


def import_annotations(value, context):
    """The author's Zotero highlights and notes into workflow/annotations.csv."""
    paper = project(context, value)
    workflows()
    from auto_research.tasks.reading import import_annotations as load
    source = str(value.get("source") or "zotero")
    return json.loads(load(str(paper), source if source == "zotero" else str(Path(source).expanduser())))


def notes_ingest(value, context):
    """The author's notes, slides and tables into workflow/world.json, unapproved."""
    paper = project(context, value)
    workflows()
    from auto_research.tasks.discovery import notes_ingest as ingest
    return json.loads(ingest(str(paper), folder=str(value.get("folder") or "workflow/notes")))


def croissant_draft(value, context):
    """Draft workflow/croissant.json from the datasheet."""
    paper = project(context, value)
    workflows()
    from auto_research.tasks.outreach import croissant_draft as draft
    return json.loads(draft(str(paper)))


def lean_draft(value, context):
    """A Lean 4 statement of one theorem of the paper, its proof left as sorry."""
    paper = project(context, value)
    workflows()
    from auto_research.tasks.outreach import lean_draft as draft
    return json.loads(draft(str(paper), str(value["label"])))


def campaign(value, context):
    """The unattended campaign: its state, or the author starting or stopping it."""
    paper = project(context, value)
    workflows()
    from auto_research.tasks import campaign as runs
    action = value.get("action") or "state"
    if action == "start":
        return runs.start_campaign(str(paper))
    if action == "stop":
        return runs.stop_campaign(str(paper))
    return runs.campaign_state(str(paper))


def set_progress(value, context):
    """The author's word on the reading: one task's state corrected, or the whole reading confirmed."""
    paper = project(context, value)
    workflows()
    from auto_research import progress as reading
    if value.get("confirm"):
        return reading.confirm(paper)
    return reading.set_task(paper, value["task"], value["state"])


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


def results_lib():
    workflows()
    from auto_research import results
    return results


def result(value, context):
    """The newest run of a task with what is left to do about it, and its earlier runs."""
    lib, paper = results_lib(), project(context, value)
    return {"result": lib.result(paper, value["task"]), "history": lib.history(paper, value["task"])}


def pending(value, context):
    """Every task whose newest run asks something of the author, and the uncommitted changes."""
    lib, paper = results_lib(), project(context, value)
    return {"results": lib.pending(paper), "changes": lib.changes(paper)}


def apply_edits(value, context):
    lib, paper = results_lib(), project(context, value)
    return lib.apply(paper, value["task"], value["run"], list(value.get("ids") or []))


def reject_edits(value, context):
    lib, paper = results_lib(), project(context, value)
    return lib.reject(paper, value["run"], list(value.get("ids") or []))


def place_file(value, context):
    lib, paper = results_lib(), project(context, value)
    return lib.place(paper, value["task"], value["run"], value["name"])


def set_aside(value, context):
    lib, paper = results_lib(), project(context, value)
    return lib.set_aside(paper, value["run"], bool(value.get("value", True)))


def answer(value, context):
    lib, paper = results_lib(), project(context, value)
    return lib.answer(paper, value["task"], value["run"], value["question"], value["option"])


def changes(value, context):
    lib, paper = results_lib(), project(context, value)
    items = lib.changes(paper)
    return {"changes": items, "message": lib.message(paper, [c["path"] for c in items])}


def diff(value, context):
    lib, paper = results_lib(), project(context, value)
    return {"path": value["path"], "diff": lib.diff(paper, value["path"])}


def revert(value, context):
    lib, paper = results_lib(), project(context, value)
    return lib.revert(paper, list(value.get("paths") or []))


def commit(value, context):
    lib, paper = results_lib(), project(context, value)
    return lib.commit(paper, list(value.get("paths") or []), value.get("message") or "")


def reveal(value, context):
    """Show a file of the project in the system's file manager, or open it with its app."""
    import sys
    paper = project(context, value)
    path = inside(paper, value["path"])
    if not path.exists():
        raise ValueError(f"{value['path']} does not exist")
    if sys.platform == "darwin":
        command = ["open", "-R", str(path)] if value.get("show") else ["open", str(path)]
    elif sys.platform.startswith("win"):
        command = ["explorer", "/select,", str(path)] if value.get("show") else ["cmd", "/c", "start", "", str(path)]
    else:
        command = ["xdg-open", str(path.parent if value.get("show") else path)]
    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"opened": value["path"]}


operations = {"projects": projects, "browse": browse, "pick_folder": pick_folder, "overview": overview,
              "run_task": run_task, "artifacts": artifacts, "pages": pages, "read_text": read_text,
              "read_file": read_file, "suggest_kind": suggest_kind, "set_brief": set_brief, "scan": scan,
              "set_progress": set_progress, "approve": approve, "result": result, "pending": pending,
              "apply_edits": apply_edits, "reject_edits": reject_edits, "place_file": place_file,
              "set_aside": set_aside, "answer": answer, "changes": changes, "diff": diff, "revert": revert,
              "commit": commit, "reveal": reveal, "preregister": preregister, "import_library": import_library,
              "apply_edit": apply_edit, "campaign": campaign, "import_tracking": import_tracking,
              "import_annotations": import_annotations, "croissant_draft": croissant_draft, "lean_draft": lean_draft,
              "notes_ingest": notes_ingest}


# A runner the host starts ahead imports this module before its operation
# arrives: load the workflows and the PDF library then, not while the author waits.
if os.environ.get("OPENPROGRAM_APPLICATION_WARM"):
    try:
        workflows()
        pdf_library()
    except Exception:  # noqa: BLE001,S110  the operation that needs them reports the error
        pass

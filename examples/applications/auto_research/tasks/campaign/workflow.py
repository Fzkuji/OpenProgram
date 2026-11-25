"""An unattended research campaign: one recurring scheduled task that, every few hours,
runs the tasks workflow/campaign.md names and then lets an agent do the agreed work,
until the budget is spent or the last day has passed.

Learned from Kosmos and Edison (multi-hour unattended runs), DeepScientist (a month-long
loop) and Agent Laboratory's autonomous mode. The step itself is plain code: it checks the
author's approval, the spend since the campaign started and the date, runs each listed
task through the task runner with its errors kept to that task, and appends a line to
workflow/campaign_log.md. The scheduler and the runner are imported inside the functions,
so the registry can import this module and tests can replace both.
"""

import json
import re
from datetime import date, datetime
from pathlib import Path

from auto_research import ledger, stages

SPEC = "campaign.md"
STATE = "campaign.json"
LOG = "campaign_log.md"
TEMPLATE = Path(__file__).resolve().parents[2] / "templates" / "campaign.md"
FIELDS = ("Goal", "Tasks", "Agent work", "Every", "Budget", "Until", "Approved")
SELF = "campaign"
LOG_HEADER = "# Campaign log\n\nOne line per step (`step`) and per agent run (`agent`), oldest first.\n\n"
STEP_LINE = re.compile(r"^- \S+ step\b", re.M)
SCHEDULED_RUN_LIMIT_S = 2 * 3600.0


# The spec, the state file and the log.

def paper_of(paper_dir: str) -> Path:
    paper = Path(paper_dir).expanduser().resolve()
    if not (paper / ".git").exists():
        raise ValueError(f"{paper} is not a Git repository")
    return paper


def read_spec(paper: Path, create: bool = True) -> dict:
    """The fields of workflow/campaign.md; a missing spec is copied from the template first."""
    path = stages.folder(paper) / SPEC
    if not path.exists():
        if not create:
            return {name: "" for name in FIELDS}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    text = path.read_text(encoding="utf-8")
    spec = {}
    for name in FIELDS:
        found = re.search(rf"^{re.escape(name)}:[ \t]*(.*)$", text, re.M | re.I)
        spec[name] = found.group(1).strip() if found else ""
    return spec


def task_names(spec: dict) -> list[str]:
    return [t.strip() for t in spec["Tasks"].split(",") if t.strip()]


def budget_of(spec: dict) -> float | None:
    found = re.search(r"\d+(?:\.\d+)?", spec["Budget"].replace(",", ""))
    return float(found.group()) if found else None


def until_of(spec: dict) -> date | None:
    value = spec["Until"].strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def approved(spec: dict) -> bool:
    return spec["Approved"].lower() in ("yes", "y", "true")


def problems(spec: dict, schedule: bool = False) -> list[str]:
    """What keeps the spec from running unattended."""
    found = [] if approved(spec) else ["workflow/campaign.md is not approved by the author (Approved: yes)"]
    names = task_names(spec)
    if not names and not spec["Agent work"]:
        found.append("Tasks and Agent work are both empty: a step would do nothing")
    try:
        from auto_research import registry
        known = set(registry.BY_NAME)
    except ImportError:
        known = None
    for name in names:
        if name == SELF:
            found.append("Tasks must not name campaign itself")
        elif known is not None and name not in known:
            found.append(f"unknown task in Tasks: {name}")
    budget = budget_of(spec)
    if budget is None or budget <= 0:
        found.append("Budget must be a positive amount in USD")
    if spec["Until"].strip() and until_of(spec) is None:
        found.append("Until must be a date (YYYY-MM-DD) or empty")
    if schedule and len(spec["Every"].split()) != 5 and not spec["Every"].startswith("@"):
        found.append("Every must be a five-field cron expression")
    return found


def load_state(paper: Path) -> dict:
    path = stages.folder(paper) / STATE
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(paper: Path, state: dict) -> dict:
    path = stages.folder(paper) / STATE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return state


def append_log(paper: Path, line: str) -> None:
    path = stages.folder(paper) / LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else LOG_HEADER
    if text and not text.endswith("\n"):
        text += "\n"
    path.write_text(text + line + "\n", encoding="utf-8")


def log_lines(paper: Path) -> list[str]:
    path = stages.folder(paper) / LOG
    if not path.exists():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("- ")]


def total_spend(paper: Path) -> float:
    return float(ledger.spend(paper).get("cost", 0) or 0)


def open_proposals(paper: Path) -> list[str]:
    """The tasks research_map proposes in workflow/world.json."""
    try:
        world = json.loads((stages.folder(paper) / "world.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [str(p.get("task", "")) for p in world.get("proposals") or [] if isinstance(p, dict)]


def now() -> datetime:
    return datetime.now()


# The two things the step reaches outside this package for.

def scheduler():
    """The host scheduler service, or None when this host has none."""
    try:
        from openprogram.runtime.scheduler import service
    except ImportError:
        return None
    return service


def run_task(paper: Path, name: str) -> dict:
    """One Auto Research task through the task runner, as its JSON result."""
    from auto_research.workflow import auto_research
    return json.loads(auto_research(str(paper), task=name))


def scheduled_run_limit() -> float:
    try:
        from openprogram.runtime.scheduler.time_limits import SCHEDULED_RUN_LIMIT_S as limit
    except ImportError:
        return SCHEDULED_RUN_LIMIT_S
    return float(limit)


def disable(state: dict) -> str:
    """Disable the campaign's scheduled task; what happened, for the log."""
    task_id = state.get("task_id")
    if not task_id:
        return ""
    service = scheduler()
    if service is None:
        return "no scheduler on this host"
    try:
        if service.get_task(task_id) is None:
            return "scheduled task already gone"
        service.update_task(task_id, {"enabled": False})
    except Exception as error:      # the step still stops; the author sees why in the log
        return f"could not disable scheduled task {task_id}: {error}"
    return f"scheduled task {task_id} disabled"


# The step.

def campaign_step(paper_dir: str) -> str:
    """Run one step of the campaign in workflow/campaign.md and say what to do next.

    Refuses (REFUSED) unless the spec is approved and complete. Stops (STOPPED) and
    disables the campaign's scheduled task once the spend since the campaign started
    reaches Budget or the day is past Until. Otherwise runs each task of Tasks through
    auto_research, an error staying with its task, appends a line to
    workflow/campaign_log.md and returns JSON with status, ran, spent, budget,
    stop_reason and next. The spend counted is ledger.spend's total minus the total
    when the campaign started (workflow/campaign.json).
    """
    paper = paper_of(paper_dir)
    spec = read_spec(paper)
    budget = budget_of(spec)
    result = {"status": "", "ran": [], "spent": 0.0, "budget": budget, "stop_reason": "", "next": ""}
    found = problems(spec)
    if found:
        result.update(status="REFUSED", stop_reason="; ".join(found), next=(
            "Write the campaign in workflow/campaign.md, show it to the author in Chinese, and set "
            "Approved: yes only after the author agrees. Then start it with start_campaign."))
        return json.dumps(result, ensure_ascii=False, indent=2)

    state = load_state(paper)
    if not state.get("started_at"):
        state = save_state(paper, {**state, "state": state.get("state") or "running",
                                   "started_at": now().isoformat(timespec="seconds"),
                                   "spend_at_start": total_spend(paper)})
    if state.get("state") == "stopped":
        result.update(status="STOPPED", spent=spent_since(paper, state),
                      stop_reason=state.get("stop_reason") or "the campaign was stopped",
                      next="The campaign is over. Do nothing further; tell the author in Chinese.")
        return json.dumps(result, ensure_ascii=False, indent=2)

    reason = over(paper, spec, state)
    if not reason:
        for name in task_names(spec):
            try:
                out = run_task(paper, name)
                row = {"task": name, "status": str(out.get("status") or "FAIL")}
                if out.get("report") or (out.get("check") or {}).get("report"):
                    row["report"] = out.get("report") or out["check"]["report"]
            except Exception as error:      # one task's failure does not end the step
                row = {"task": name, "status": "ERROR", "error": f"{type(error).__name__}: {error}"[:300]}
            result["ran"].append(row)
        reason = over(paper, spec, state, after=True)

    spent = spent_since(paper, state)
    result["spent"] = spent
    if reason:
        note = disable(state)
        save_state(paper, {**state, "state": "stopped", "stop_reason": reason,
                           "stopped_at": now().isoformat(timespec="seconds")})
        result.update(status="STOPPED", stop_reason=reason,
                      next="The campaign is over. Do no Agent work; tell the author in Chinese why it stopped.")
        if note:
            result["scheduler"] = note
    else:
        result.update(status="RAN", next=(
            f"Do the Agent work: {spec['Agent work']}" if spec["Agent work"] else "No Agent work; this run is done."))
    tasks = ", ".join(f"{r['task']} {r['status']}" for r in result["ran"]) or "none"
    proposals = open_proposals(paper)
    stopped = f" | STOPPED: {reason}" if reason else ""
    append_log(paper, f"- {now().isoformat(timespec='seconds')} step | tasks: {tasks} | spent "
                      f"${spent:.2f} of ${budget:.2f} | proposals open: {len(proposals)}"
                      f"{' (' + ', '.join(proposals) + ')' if proposals else ''}{stopped}")
    return json.dumps(result, ensure_ascii=False, indent=2)


def spent_since(paper: Path, state: dict) -> float:
    return round(max(0.0, total_spend(paper) - float(state.get("spend_at_start") or 0)), 6)


def over(paper: Path, spec: dict, state: dict, after: bool = False) -> str:
    """Why the campaign must stop now, or ""."""
    budget = budget_of(spec) or 0
    spent = spent_since(paper, state)
    if spent >= budget:
        return f"budget reached: ${spent:.2f} of ${budget:.2f}"
    until = until_of(spec)
    if not after and until and now().date() > until:
        return f"past Until ({until.isoformat()})"
    return ""


# Starting, stopping and reading the campaign.

def title_of(paper: Path) -> str:
    return f"Auto Research campaign: {paper.name}"


def prompt_of(paper: Path, spec: dict) -> str:
    hours = scheduled_run_limit() / 3600
    return f"""You run one step of an unattended research campaign for the paper in {paper}.
Nobody is watching; this run stops after {hours:g} hours.

1. Run the campaign step and read its JSON:
   openprogram programs run auto_research -a paper_dir={paper} -a task=campaign
2. If its status is STOPPED or REFUSED, stop here: do nothing else.
3. Otherwise do the Agent work of workflow/campaign.md: {spec['Agent work'] or '(none; stop here)'}
   The campaign's goal: {spec['Goal'] or '(see workflow/campaign.md)'}
   - Change only the files the Edit line of workflow/experiment.md lists, and follow its rules.
   - Commit only on a branch named campaign/<today's date, YYYY-MM-DD> (create it from the
     current branch if it does not exist). Never push, never merge, never rewrite history.
   - Never approve anything on the author's behalf: leave every Approved: line as it is.
   - Keep within the budget the step reports; stop early when the work is done.
4. Append one line to workflow/campaign_log.md:
   - <ISO time> agent | <what you did, the branch and commits, results, what is left>
"""


def start_campaign(paper_dir: str) -> dict:
    """Create the campaign's one recurring scheduled task, or return the running one.

    Refuses while workflow/campaign.md is not approved or complete. The task (titled
    "Auto Research campaign: <folder>", working in the paper, on the Every cron) prompts an
    unattended agent to run the campaign step and then do the Agent work. Its id, the start
    time and the spend at the start are kept in workflow/campaign.json.
    """
    paper = paper_of(paper_dir)
    spec = read_spec(paper)
    found = problems(spec, schedule=True)
    if found:
        return {"status": "REFUSED", "problems": found}
    service = scheduler()
    if service is None:
        return {"status": "REFUSED", "problems": ["this host has no scheduler"]}
    state = load_state(paper)
    existing = None
    if state.get("task_id"):
        existing = service.get_task(state["task_id"])
    if existing is None:
        existing = next((t for t in service.list_tasks() if t.get("title") == title_of(paper)), None)
    if existing is not None and state.get("state") == "running":
        if not existing.get("enabled", True):
            service.update_task(existing["id"], {"enabled": True})
        if state.get("task_id") != existing["id"]:
            save_state(paper, {**state, "task_id": existing["id"]})
        return {"status": "RUNNING", "task_id": existing["id"], "created": False}
    if existing is not None:      # left over from a stopped campaign
        service.delete_task(existing["id"])
    task = service.create_task(title=title_of(paper), task_type="recurring", prompt=prompt_of(paper, spec),
                               cron=spec["Every"], cwd=str(paper),
                               notes="Auto Research campaign; workflow/campaign.md is its spec. "
                                     "Stop it with stop_campaign.")
    state = save_state(paper, {"state": "running", "task_id": task["id"],
                               "started_at": now().isoformat(timespec="seconds"),
                               "spend_at_start": total_spend(paper)})
    append_log(paper, f"- {state['started_at']} start | scheduled task {task['id']} every "
                      f"{spec['Every']} | budget ${budget_of(spec):.2f}")
    return {"status": "RUNNING", "task_id": task["id"], "created": True}


def stop_campaign(paper_dir: str) -> dict:
    """Delete the campaign's scheduled task and mark the campaign stopped."""
    paper = paper_of(paper_dir)
    state = load_state(paper)
    deleted = False
    service = scheduler()
    if service is not None:
        ids = {state.get("task_id")} | {t.get("id") for t in service.list_tasks()
                                        if t.get("title") == title_of(paper)}
        for task_id in sorted(i for i in ids if i):
            deleted = bool(service.delete_task(task_id)) or deleted
    if state:
        stamp = now().isoformat(timespec="seconds")
        save_state(paper, {**state, "state": "stopped", "stop_reason": state.get("stop_reason") or "stopped by the author",
                           "stopped_at": state.get("stopped_at") or stamp})
        append_log(paper, f"- {stamp} stop | scheduled task {'deleted' if deleted else 'not found'}")
    return {"status": "STOPPED", "deleted": deleted}


def campaign_state(paper_dir: str) -> dict:
    """Running or stopped, steps done, spend against budget and the last log lines. Writes nothing."""
    paper = paper_of(paper_dir)
    spec = read_spec(paper, create=False)
    state = load_state(paper)
    lines = log_lines(paper)
    running = state.get("state") == "running"
    task = None
    if running:
        service = scheduler() if state.get("task_id") else None
        task = service.get_task(state["task_id"]) if service is not None else None
        running = task is not None and bool(task.get("enabled", True))
    return {"state": "running" if running else "stopped", "approved": approved(spec),
            "task_id": state.get("task_id"), "every": spec["Every"], "until": spec["Until"],
            "started_at": state.get("started_at"), "stop_reason": state.get("stop_reason", ""),
            "steps": sum(1 for line in lines if STEP_LINE.match(line)),
            "spent": spent_since(paper, state) if state else 0.0, "budget": budget_of(spec),
            "last": lines[-5:]}


__all__ = ["campaign_state", "campaign_step", "start_campaign", "stop_campaign"]

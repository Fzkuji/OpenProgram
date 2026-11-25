"""What the scouting tasks share: the paper folder, the run folder, the paper's title and
abstract, workflow fields and one POST request. Plain code, no model calls."""

import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from auto_research import brief, stages
from auto_research.tasks.citations import sources
from auto_research.tasks.submission import common as tex


def paper_folder(paper_dir: str) -> Path:
    paper = Path(paper_dir).expanduser().resolve()
    if not paper.is_dir():
        raise ValueError(f"paper_dir is not a folder: {paper}")
    return paper


def run_folder(paper: Path, out_dir: str, task: str) -> Path:
    base = Path(out_dir).expanduser() if out_dir else paper / "drafts" / task
    out = base / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    out.mkdir(parents=True, exist_ok=True)
    return out


def latex_text(s: str) -> str:
    s = re.sub(r"\\(?:cite\w*|ref|label|eqref)\s*\{[^}]*\}", "", s)
    s = re.sub(r"\\[a-zA-Z]+\*?\s*(?:\[[^\]]*\])?", " ", s)
    return re.sub(r"\s+", " ", s.replace("{", "").replace("}", "").replace("~", " ")).strip()


def paper_text(paper: Path) -> tuple[str, str]:
    """The paper's title and abstract from its main .tex; the project's claim stands in
    for the abstract when there is no .tex."""
    title = abstract = ""
    try:
        main = tex.main_tex(paper)
        source = tex.sources(paper, main) if main else ""
    except (OSError, ValueError):
        source = ""
    if source:
        m = re.search(r"\\title\s*(?:\[[^\]]*\])?\s*\{((?:[^{}]|\{[^{}]*\})*)\}", source)
        title = latex_text(m.group(1)) if m else ""
        m = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", source, re.S)
        abstract = latex_text(m.group(1)) if m else ""
    if not abstract:
        abstract = brief.read(paper)["claim"]
    return title, abstract


def project_field(paper: Path, key: str) -> str:
    """A `Key: value` line of workflow/project.md, or ""."""
    path = stages.folder(paper) / "project.md"
    text = path.read_text(encoding="utf-8", errors="ignore") if path.is_file() else ""
    m = re.search(rf"^\s*(?:[-*]\s*)?{re.escape(key)}\s*:[ \t]*(\S.*)$", text, re.M | re.I)
    return m.group(1).strip() if m else ""


def rules(paper: Path) -> dict:
    path = stages.folder(paper) / "rules.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except ValueError:
        return {}


RETRIES = 2        # a rate-limited or failing POST is tried again after RETRY_WAIT, 2·RETRY_WAIT s
RETRY_WAIT = 3.0


def post(url: str, params: dict, body: dict, label: str = "", headers: dict | None = None):
    """POST JSON and read JSON back; None (and the label in UNAVAILABLE) on failure."""
    import urllib.parse
    full = url + ("?" + urllib.parse.urlencode(params) if params else "")
    request = urllib.request.Request(full, data=json.dumps(body).encode(), method="POST",
                                     headers={**sources.UA, "Content-Type": "application/json", **(headers or {})})
    for attempt in range(RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=sources.TIMEOUT) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or attempt == RETRIES:
                break
        except (urllib.error.URLError, TimeoutError):
            if attempt == RETRIES:
                break
        except Exception:  # noqa: BLE001  any other failure: the source is unavailable
            break
        time.sleep(RETRY_WAIT * (attempt + 1))
    if label:
        sources.UNAVAILABLE.add(label)
    return None


def cell(text, limit: int = 0) -> str:
    s = " ".join(str(text or "").split()).replace("|", "\\|")
    return s[:limit - 1] + "…" if limit and len(s) > limit else s


__all__ = ["cell", "paper_folder", "paper_text", "post", "project_field", "rules", "run_folder"]

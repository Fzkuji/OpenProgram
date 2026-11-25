"""What paper_agent and talk_prep share: the output folder, schema pieces, evidence.csv
and the numbers of a text. Plain code, no model calls."""

import csv
import io
import json
import re
from datetime import datetime
from pathlib import Path

from auto_research import stages

from ..media.workflow import plain

NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


def out_folder(paper: Path, out_dir: str, default: str) -> Path:
    base = Path(out_dir).expanduser() if out_dir else paper / "drafts" / default
    return base / datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def evidence_rows(paper: Path) -> dict[str, dict]:
    """The rows of workflow/evidence.csv by id (empty when there is no file)."""
    path = stages.folder(paper) / "evidence.csv"
    if not path.is_file():
        return {}
    with io.StringIO(path.read_text(encoding="utf-8"), newline="") as f:
        rows = [{k: (v or "").strip() for k, v in r.items() if k} for r in csv.DictReader(f)]
    return {r["id"]: r for r in rows if r.get("id")}


def values_in(text: str) -> list[float]:
    """Every number written in a text; 1{,}000 and 1,000 read as 1000."""
    return [float(m) for m in NUMBER.findall(plain(text))]


def load(reply):
    return json.loads(reply) if isinstance(reply, str) else reply


def _str(limit: int, least: int = 1) -> dict:
    return {"type": "string", "minLength": least, "maxLength": limit}


def _arr(items: dict, least: int, most: int) -> dict:
    return {"type": "array", "items": items, "minItems": least, "maxItems": most}


def _obj(props: dict) -> dict:
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}


def _enum(values) -> dict:
    return {"type": "string", "enum": list(values)}


__all__ = ["evidence_rows", "load", "out_folder", "values_in"]

"""croissant_check on a valid and an invalid fixture (plain validation and a stand-in
mlcroissant), and croissant_draft from a datasheet that names a CSV file."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from auto_research.tasks.outreach import croissant as cr

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def paper(tmp_path, monkeypatch):
    repo = tmp_path / "paper"
    (repo / "workflow").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "main.tex").write_text("\\documentclass{article}\n\\title{Tiny Scores}\n\\begin{document}\nHi there.\n"
                                   "\\end{document}\n")
    shutil.copy(FIXTURES / "data.csv", repo / "workflow" / "data.csv")
    monkeypatch.setattr(cr, "load_mlcroissant", lambda: None)
    return repo


def check(paper, name):
    shutil.copy(FIXTURES / name, paper / "workflow" / "croissant.json")
    out = json.loads(cr.croissant_check(str(paper)))
    return out, json.loads(Path(out["json"]).read_text())


def test_valid_croissant_passes(paper):
    out, record = check(paper, "croissant_valid.json")
    assert out["status"] == "PASS" and out["validator"] == "plain", record["findings"]
    assert out["report"].startswith(str(paper / "drafts" / "croissant_check"))
    trail = json.loads(Path(out["run"]).read_text())
    assert [s["stage"] for s in trail["stages"]] == ["validate", "report"] and trail["roles"] == {}


def test_invalid_croissant_fails_with_each_problem(paper):
    out, record = check(paper, "croissant_invalid.json")
    assert out["status"] == "FAIL"
    errors = {f["where"] for f in record["findings"] if f["level"] == "error"}
    assert {"@type", "conformsTo", "license", "distribution[0].encodingFormat", "distribution[0].sha256",
            "distribution[1].includes", "distribution[1].containedIn", "distribution[1].encodingFormat",
            "recordSet[0].field[0].dataType", "recordSet[0].field[1].source",
            "rai:dataBiases", "rai:dataLimitations"} <= errors
    sha = next(f for f in record["findings"] if f["where"] == "distribution[0].sha256")
    assert "不符" in sha["problem"]                                    # local file checked against its sha256
    notes = {f["where"] for f in record["findings"] if f["level"] == "note"}
    assert "rai:dataSocialImpact" in notes
    assert "rai:dataBiases" in Path(out["report"]).read_text()


def test_missing_and_broken_files(paper):
    out = json.loads(cr.croissant_check(str(paper)))
    assert out["status"] == "FAIL" and out["errors"] == 1
    (paper / "meta.json").write_text("{not json")
    out = json.loads(cr.croissant_check(str(paper), path="meta.json"))
    assert out["status"] == "FAIL"
    assert "不是合法 JSON" in Path(out["report"]).read_text()


def test_mlcroissant_is_used_when_importable(paper, monkeypatch):
    class Issues:
        warnings = ["Property sc:keywords is recommended"]

    class Dataset:
        def __init__(self, jsonld):
            data = json.loads(Path(jsonld).read_text())
            if "license" not in data:
                raise ValueError("Found the following 1 error(s):\n  -  Property sc:license is mandatory")
            self.metadata = type("M", (), {"ctx": type("C", (), {"issues": Issues()})()})()

    monkeypatch.setattr(cr, "load_mlcroissant", lambda: type("mlc", (), {"Dataset": Dataset}))
    out, record = check(paper, "croissant_valid.json")
    assert out["validator"] == "mlcroissant" and out["status"] == "PASS_WITH_NOTES"
    assert record["findings"] == [{"level": "note", "where": "mlcroissant",
                                   "problem": "Property sc:keywords is recommended", "fix": ""}]
    out, record = check(paper, "croissant_invalid.json")
    problems = [f["problem"] for f in record["findings"] if f["level"] == "error"]
    assert "Property sc:license is mandatory" in problems
    assert any("rai:dataBiases" in p for p in problems)               # RAI is checked in plain code too


DATASHEET = """# Datasheet
License: CC-BY-4.0
Location: workflow/data.csv
Creator: Jane Doe, John Roe

## Motivation
Two scored sentences to test loaders.
## Composition
Two rows.
## Collection process
Written by hand.
## Preprocessing
None.
## Uses
Testing loaders.
## Distribution
Also at https://example.org/tiny/data.csv and `workflow/data.csv`.
## Maintenance
Kept as is.
## Known biases
English only.
"""


def test_draft_from_the_datasheet(paper):
    (paper / "workflow" / "datasheet.md").write_text(DATASHEET)
    out = json.loads(cr.croissant_draft(str(paper)))
    assert out["status"] == "PASS_WITH_NOTES" and out["workflow_file"] == str(paper / "workflow" / "croissant.json")
    doc = json.loads((paper / "workflow" / "croissant.json").read_text())
    assert doc["name"] == "Tiny Scores" and doc["license"] == "CC-BY-4.0"
    assert [c["name"] for c in doc["creator"]] == ["Jane Doe", "John Roe"]
    assert doc["rai:dataCollection"] == "Written by hand." and doc["rai:dataBiases"] == "English only."
    assert doc["rai:personalSensitiveInformation"].startswith("TODO")
    local, remote = doc["distribution"]
    assert local["sha256"] == cr.sha256(paper / "workflow" / "data.csv") and local["encodingFormat"] == "text/csv"
    assert local["contentUrl"] == "../workflow/data.csv" and "_local" not in local
    assert remote["contentUrl"] == "https://example.org/tiny/data.csv" and remote["sha256"].startswith("TODO")
    fields = doc["recordSet"][0]["field"]
    assert [(f["name"], f["dataType"]) for f in fields] == [("id", "sc:Integer"), ("text", "sc:Text"),
                                                            ("score", "sc:Float")]
    assert fields[0]["source"] == {"fileObject": {"@id": local["@id"]}, "extract": {"column": "id"}}
    # The draft checks clean apart from its TODOs; a second draft does not overwrite the first.
    record = json.loads(Path(json.loads(cr.croissant_check(str(paper)))["json"]).read_text())
    errors = {f["where"] for f in record["findings"] if f["level"] == "error"}
    assert errors == {"url", "rai:personalSensitiveInformation", "rai:dataLimitations", "distribution[1].sha256"}
    again = json.loads(cr.croissant_draft(str(paper)))
    assert again["workflow_file"] == "" and Path(again["croissant"]).is_file()


def test_draft_without_a_datasheet(paper):
    out = json.loads(cr.croissant_draft(str(paper)))
    doc = json.loads(Path(out["croissant"]).read_text())
    assert doc["license"].startswith("TODO") and doc["distribution"][0]["contentUrl"].startswith("TODO")
    assert doc["recordSet"][0]["field"][0]["dataType"].startswith("TODO")
    assert "没有 workflow/datasheet.md" in Path(out["report"]).read_text()

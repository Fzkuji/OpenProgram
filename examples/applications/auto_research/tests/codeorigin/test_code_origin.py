"""code_origin on the fixture repository with a fake Software Heritage and scripted roles."""

import csv
import json

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.codeorigin import code_origin
from auto_research.tasks.codeorigin import swh
from auto_research.tasks.codeorigin import workflow as wf
from auto_research.tasks.codeorigin.files import sha1_git

from .repo import GPT, TRAINER, UPSTREAM_MIT, FakeArchive, make

MINGPT = "https://github.com/karpathy/minGPT"
NONE = {"files": []}


def archive(**kw):
    defaults = {"known": {sha1_git(GPT.encode())},
                "origins": {MINGPT: {"mingpt/model.py": GPT, "mingpt/trainer.py": TRAINER, "LICENSE": UPSTREAM_MIT}}}
    return FakeArchive(**{**defaults, **kw})


def identify_reply():
    return {"files": [
        {"file": "models/gpt.py", "candidates": [{"origin": "https://github.com/nobody/nothing", "path": "gpt.py"},
                                                 {"origin": MINGPT + "/blob/master/mingpt/model.py", "path": ""}]},
        {"file": "utils/color.py", "candidates": [{"origin": "https://github.com/ansible/ansible",
                                                   "path": "lib/ansible/utils/color.py"}]},
        {"file": "not/asked.py", "candidates": [{"origin": MINGPT, "path": "x.py"}]}]}


def credit_reply():
    return {"files": [
        {"file": "models/gpt.py", "credit": "missing", "where": "", "quote": "", "reason": "没有任何署名",
         "copyright": "Copyright (c) 2020 Andrej Karpathy", "note": "GPT model from minGPT, unchanged."},
        {"file": "utils/color.py", "credit": "adequate", "where": "file", "quote": "# This file is part of Ansible",
         "reason": "保留了 GPL 声明", "copyright": "(c) 2012-2014, Michael DeHaan <michael.dehaan@gmail.com>",
         "note": ""},
        {"file": "third_party/fastlib/ops.py", "credit": "adequate", "where": "file",
         "quote": "#  Copyright (c) 2020   Fastlib Developers", "reason": "保留版权行", "copyright": "Made Up Inc.",
         "note": "NumPy helpers."},
        {"file": "train.py", "credit": "adequate", "where": "readme", "quote": "Uses fastlib.", "reason": "README 提到",
         "copyright": "", "note": ""}],
        "readme_line": "Includes code from https://github.com/somewhere/else (MIT)."}


def table(path):
    with open(path, encoding="utf-8") as f:
        return {r["file"]: r for r in csv.DictReader(f)}


def test_copied_gpl_and_vendored_code_are_found_checked_and_credited(tmp_path, monkeypatch):
    paper = make(tmp_path)
    fake = archive()
    monkeypatch.setattr(swh, "Archive", lambda: fake)
    with script_roles({wf.identifier: [identify_reply()], wf.creditor: [credit_reply()]}) as calls:
        out = json.loads(code_origin(str(paper)))
    assert out["status"] == "FAIL" and out["repo_license"] == "MIT"
    assert out["conflicts"] == ["utils/color.py: GPL-3.0-or-later vs MIT"]
    rows = table(out["origin"])
    assert "drafts/old.py" not in rows and rows["weights.bin"]["status"] == "skipped"
    gpt = rows["models/gpt.py"]
    assert gpt["status"] == "known" and gpt["swhid"] == "swh:1:cnt:" + sha1_git(GPT.encode())
    assert gpt["origin"] == f"{MINGPT} (mingpt/model.py)" and gpt["license"] == "MIT" and gpt["compatible"] == "yes"
    assert gpt["credit"] == "missing"
    trainer = rows["models/trainer.py"]                    # found next to model.py in the verified origin
    assert trainer["status"] == "adapted" and trainer["origin"] == f"{MINGPT} (mingpt/trainer.py)"
    assert trainer["license"] == "MIT" and "指纹相同" in trainer["evidence"]
    color = rows["utils/color.py"]
    assert color["status"] == "adapted" and color["compatible"] == "no" and color["credit"] == "adequate"
    ops = rows["third_party/fastlib/ops.py"]
    assert ops["status"] == "adapted" and ops["license"] == "BSD-3-Clause" and ops["credit"] == "adequate"
    train = rows["train.py"]
    assert train["status"] == "adapted" and "third_party/fastlib/ops.py" in train["evidence"]
    assert train["license"] == "BSD-3-Clause" and train["credit"] == "missing"        # its quote is not in README
    assert rows["pkg/__init__.py"]["status"] == "trivial" and rows["LICENSE"]["status"] in ("unique", "trivial")
    # The identifier saw only third-party files without an origin; unknown files and fake origins are dropped.
    asked = calls[wf.identifier][0].text
    assert "models/gpt.py" in asked and "utils/color.py" in asked and "Software Heritage" in asked
    assert "third_party/fastlib/ops.py" in asked and "pkg/__init__.py" not in asked
    notices = open(out["notices"], encoding="utf-8").read()
    assert "## https://github.com/karpathy/minGPT" in notices and "Copyright (c) 2020 Andrej Karpathy" in notices
    assert "Permission is hereby granted" in notices and "Made Up Inc." not in notices
    assert "    (c) 2012-2014, Michael DeHaan" in notices and "`models/trainer.py`: adapted (from `mingpt/trainer.py`)" in notices
    assert "GPL-3.0-or-later" in notices and "Origin not identified" in notices
    edits = json.load(open(out["edits"], encoding="utf-8"))
    kinds = {e["file"]: e for e in edits["edits"]}
    assert kinds["THIRD_PARTY_NOTICES.md"]["kind"] == "create"
    readme = kinds["README.md"]
    assert readme["quote"] == "Train the probe with `python train.py`." and "somewhere/else" not in readme["replacement"]
    assert "THIRD_PARTY_NOTICES.md" in readme["replacement"] and MINGPT in readme["replacement"]
    report = open(out["report"], encoding="utf-8").read()
    assert "代码来源与许可证" in report and "FAIL" in report and "cp '" in report and "冲突" in report
    run = json.load(open(out["run"], encoding="utf-8"))
    assert [s["stage"] for s in run["stages"]] == ["files", "software heritage", "similarity", "identify",
                                                   "origins", "licenses", "credit", "report"]
    verified = {(v["origin"], v["result"]) for v in run["stages"][4]["verified"]}
    assert (MINGPT, "exact") in verified and ("https://github.com/nobody/nothing", "not archived") in verified
    assert run["stages"][4]["siblings"] == ["models/trainer.py"]


def test_an_archive_that_cannot_be_asked_is_named_and_local_evidence_still_counts(tmp_path, monkeypatch):
    paper = make(tmp_path, gpl=False)
    monkeypatch.setattr(swh, "Archive", lambda: FakeArchive(problem="匿名请求已达每小时上限"))
    reply = {"files": [{"file": f, "credit": "adequate", "where": "file", "quote": "Copyright (c) 2020 Fastlib Developers",
                        "reason": "", "copyright": "", "note": ""} for f in ("third_party/fastlib/ops.py",)],
             "readme_line": ""}
    with script_roles({wf.creditor: [reply]}) as calls:
        out = json.loads(code_origin(str(paper)))
    assert wf.identifier not in calls or not calls[wf.identifier]
    assert out["status"] == "PASS_WITH_NOTES" and out["unavailable_sources"] == ["Software Heritage：匿名请求已达每小时上限"]
    rows = table(out["origin"])
    assert rows["models/gpt.py"]["status"] == "unique"                 # nothing local says it was copied
    assert rows["train.py"]["status"] == "adapted"
    assert "Software Heritage" in open(out["report"], encoding="utf-8").read()


def test_credited_permissive_code_passes(tmp_path, monkeypatch):
    paper = make(tmp_path, gpl=False, copied=False)
    (paper / "train.py").write_text("import numpy as np\n\n\ndef main():\n    return np.zeros(3)\n" * 3)
    (paper / "README.md").write_text("# Probe\n\nfastlib (BSD-3-Clause) is vendored in third_party/fastlib.\n")
    monkeypatch.setattr(swh, "Archive", lambda: FakeArchive())
    reply = {"files": [{"file": "third_party/fastlib/ops.py", "credit": "adequate", "where": "readme",
                        "quote": "fastlib (BSD-3-Clause) is vendored", "reason": "", "copyright": "", "note": ""}],
             "readme_line": "fastlib is BSD-3-Clause."}
    with script_roles({wf.identifier: [NONE], wf.creditor: [reply]}):
        out = json.loads(code_origin(str(paper), code_dir=str(paper), out_dir=str(tmp_path / "out")))
    assert out["status"] == "PASS" and out["uncredited"] == [] and out["report"].startswith(str(tmp_path / "out"))


def test_own_archived_files_are_not_called_copies(tmp_path, monkeypatch):
    paper = make(tmp_path, gpl=False)
    own = "https://github.com/me/probe"
    import subprocess
    subprocess.run(["git", "-C", str(paper), "remote", "add", "origin", "git@github.com:me/probe.git"], check=True)
    fake = archive(origins={own: {"models/gpt.py": GPT}})
    monkeypatch.setattr(swh, "Archive", lambda: fake)
    reply = {"files": [], "readme_line": ""}
    with script_roles({wf.identifier: [NONE], wf.creditor: [reply]}):
        out = json.loads(code_origin(str(paper)))
    assert table(out["origin"])["models/gpt.py"]["status"] == "own"

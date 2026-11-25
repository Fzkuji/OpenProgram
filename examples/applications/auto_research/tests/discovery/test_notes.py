"""notes_ingest over Markdown, CSV, PPTX, XLSX, DOCX and PDF notes made in the test, with
the extractor scripted; approved world-model entries are never changed."""

import json
import shutil
import subprocess
import zipfile

import pytest

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.discovery import notes as wf

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"

MEETING = """# Group meeting

Warmup for 500 steps raised accuracy on LongMemEval.
Gradient clipping did not matter.
"""
WORLD = {"entities": [{"id": "LME", "name": "LongMemEval", "type": "dataset", "note": "approved by the author"},
                      {"id": "acc", "name": "accuracy", "type": "metric", "note": "", "approved": True}],
         "relations": [], "open_questions": [], "proposals": [], "at": "2026-11-01T10:00:00", "head": "abc"}


def rels(*items):
    return (f'<Relationships xmlns="{REL}">' + "".join(
        f'<Relationship Id="{i}" Type="{R}/{t}" Target="{target}"/>' for i, t, target in items) + "</Relationships>")


def slide(*texts):
    paras = "".join(f"<a:p><a:r><a:t>{t}</a:t></a:r></a:p>" for t in texts)
    return (f'<p:sld xmlns:p="{P}" xmlns:a="{A}"><p:cSld><p:spTree><p:sp><p:txBody>{paras}'
            "</p:txBody></p:sp></p:spTree></p:cSld></p:sld>")


def make_pptx(path):
    with zipfile.ZipFile(path, "w") as z:
        # slide2.xml is shown first: the order comes from presentation.xml, not the file names.
        z.writestr("ppt/presentation.xml", f'<p:presentation xmlns:p="{P}" xmlns:r="{R}"><p:sldIdLst>'
                                           '<p:sldId id="256" r:id="rId2"/><p:sldId id="257" r:id="rId1"/>'
                                           "</p:sldIdLst></p:presentation>")
        z.writestr("ppt/_rels/presentation.xml.rels", rels(("rId1", "slide", "slides/slide1.xml"),
                                                           ("rId2", "slide", "slides/slide2.xml")))
        z.writestr("ppt/slides/slide2.xml", slide("Project overview"))
        z.writestr("ppt/slides/slide1.xml", slide("Results", "Memory distillation halves the memory footprint."))
        z.writestr("ppt/slides/_rels/slide1.xml.rels", rels(("rId1", "notesSlide", "../notesSlides/notesSlide1.xml")))
        z.writestr("ppt/notesSlides/notesSlide1.xml", slide("Say that distillation needs a teacher model.", "2"))


def make_xlsx(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook xmlns="{S}" xmlns:r="{R}"><sheets>'
                                      '<sheet name="Results" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", rels(("rId1", "worksheet", "worksheets/sheet1.xml")))
        z.writestr("xl/sharedStrings.xml", f'<sst xmlns="{S}"><si><t>method</t></si><si><t>accuracy</t></si>'
                                           "<si><t>warmup</t></si></sst>")
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{S}"><sheetData>'
                   '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
                   '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>74.5</v></c></row>'
                   '<row r="4"><c r="A4" t="inlineStr"><is><t>baseline</t></is></c><c r="C4"><v>71.2</v></c></row>'
                   "</sheetData></worksheet>")


def make_docx(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", f'<w:document xmlns:w="{W}"><w:body>'
                   "<w:p><w:r><w:t>Call with Bob</w:t></w:r></w:p><w:p/>"
                   '<w:p><w:r><w:t xml:space="preserve">Distillation </w:t></w:r>'
                   "<w:r><w:t>depends on a strong teacher.</w:t></w:r></w:p></w:body></w:document>")


def make_pdf(path, pages):
    objs = ["<< /Type /Catalog /Pages 2 0 R >>",
            "<< /Type /Pages /Kids [" + " ".join(f"{4 + 2 * i} 0 R" for i in range(len(pages))) +
            f"] /Count {len(pages)} >>",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    for i, text in enumerate(pages):
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET"
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {5 + 2 * i} 0 R "
                    "/Resources << /Font << /F1 3 0 R >> >> >>")
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
    out, offsets = "%PDF-1.4\n", []
    for k, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{k} 0 obj\n{body}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    path.write_bytes(out.encode("latin-1"))


@pytest.fixture
def paper(tmp_path):
    repo = tmp_path / "paper"
    notes = repo / "workflow" / "notes"
    notes.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (notes / "meeting.md").write_text(MEETING)
    (notes / "runs.csv").write_text("method,accuracy\nwarmup,74.5\n")
    (notes / "photo.png").write_bytes(b"\x89PNG")
    make_pptx(notes / "deck.pptx")
    make_xlsx(notes / "table.xlsx")
    make_docx(notes / "call.docx")
    (repo / "workflow" / "world.json").write_text(json.dumps(WORLD))
    return repo


def where(chunks, file):
    return next(c["id"] for c in chunks if c["file"].endswith(file))


def item(chunk, quote, **kw):
    return dict(kw, chunk=chunk, quote=quote)


def test_readers_and_locators(paper):
    notes = paper / "workflow" / "notes"
    assert wf.read_pptx(notes / "deck.pptx") == [
        ("slide", 1, "Project overview"), ("slide", 2, "Results"),
        ("slide", 2, "Memory distillation halves the memory footprint."),
        ("slide", 2, "[notes] Say that distillation needs a teacher model.")]
    assert wf.read_xlsx(notes / "table.xlsx") == [("Results!r", 1, "method\taccuracy"), ("Results!r", 2, "warmup\t74.5"),
                                                  ("Results!r", 4, "baseline\t\t71.2")]
    assert wf.read_docx(notes / "call.docx") == [("para", 1, "Call with Bob"),
                                                 ("para", 2, "Distillation depends on a strong teacher.")]
    docs, skipped = wf.read_folder(paper, notes)
    assert [d["file"] for d in docs] == ["workflow/notes/call.docx", "workflow/notes/deck.pptx",
                                        "workflow/notes/meeting.md", "workflow/notes/runs.csv",
                                        "workflow/notes/table.xlsx"]
    assert skipped == [{"file": "workflow/notes/photo.png", "reason": ".png is not read"}]
    chunks = wf.chunk(docs)
    md = next(c for c in chunks if c["file"].endswith("meeting.md"))
    assert md["locator"] == "L1-4"
    assert wf.find_quote(md, "raised  accuracy on LongMemEval. Gradient clipping") == "L3-4"
    assert wf.find_quote(md, "accuracy dropped sharply") is None
    deck = next(c for c in chunks if c["file"].endswith("deck.pptx"))
    assert wf.find_quote(deck, "halves the memory footprint") == "slide2"


@pytest.mark.skipif(not shutil.which("pdftotext"), reason="pdftotext is not installed")
def test_pdf_pages(tmp_path):
    make_pdf(tmp_path / "talk.pdf", ["Intro slide", "Clipping hurts recall"])
    assert wf.read_pdf(tmp_path / "talk.pdf") == [("p", 1, "Intro slide"), ("p", 2, "Clipping hurts recall")]


def test_notes_merge_into_the_world_model_unapproved(paper):
    chunks = wf.chunk(wf.read_folder(paper, paper / "workflow" / "notes")[0])
    md, deck, sheet, call = (where(chunks, f) for f in ("meeting.md", "deck.pptx", "table.xlsx", "call.docx"))
    reply = {
        "entities": [
            item(md, "Warmup for 500 steps", name="warmup", type="method", note="500 步 warmup"),
            item(md, "on LongMemEval", name="LongMemEval", type="dataset", note="would overwrite"),
            item(deck, "Memory distillation halves", name="memory distillation", type="method", note=""),
            item(call, "a strong teacher", name="teacher model", type="concept", note=""),
            item(md, "Warmup for 1000 steps", name="long warmup", type="method", note="not in the notes"),
        ],
        "relations": [
            item(md, "Warmup for 500 steps raised accuracy", **{"from": "warmup", "to": "acc", "type": "improves"},
                 note=""),
            item(sheet, "warmup\t74.5", **{"from": "warmup", "to": "accuracy", "type": "measured_by"}, note=""),
            item(call, "Distillation depends on a strong teacher.",
                 **{"from": "memory distillation", "to": "teacher model", "type": "depends_on"}, note=""),
            item(md, "Gradient clipping did not matter.", **{"from": "clipping", "to": "acc", "type": "hurts"},
                 note=""),
        ],
        "conclusions": [item(md, "Gradient clipping did not matter.", statement="梯度裁剪没有影响。")],
    }
    with script_roles({wf.extractor: [reply]}) as calls:
        out = json.loads(wf.notes_ingest(str(paper)))
    ask = calls[wf.extractor][0]
    assert "verbatim" in ask.text and "LME: LongMemEval (dataset)" in ask.text
    assert f"[{md}] workflow/notes/meeting.md L1-4" in ask.text and "Results!r1-4" in ask.text

    world = json.loads((paper / "workflow" / "world.json").read_text())
    assert world["entities"][:2] == WORLD["entities"]                       # approved entries untouched
    assert world["at"] == WORLD["at"] and world["head"] == "abc"
    new = {e["name"]: e for e in world["entities"][2:]}
    assert set(new) == {"warmup", "memory distillation", "teacher model", "梯度裁剪没有影响。"}
    assert all(e["approved"] is False for e in new.values())
    assert new["warmup"]["source"] == "notes:workflow/notes/meeting.md#L3"
    assert new["warmup"]["quote"] == "Warmup for 500 steps" and new["warmup"]["id"] == "warmup"
    assert new["memory distillation"]["source"] == "notes:workflow/notes/deck.pptx#slide2"
    assert new["memory distillation"]["id"] == "memory_distillation"
    assert new["teacher model"]["source"] == "notes:workflow/notes/call.docx#para2"
    assert new["梯度裁剪没有影响。"]["type"] == "result" and new["梯度裁剪没有影响。"]["source"].endswith("#L4")
    rel = {(r["from"], r["type"], r["to"]): r for r in world["relations"]}
    assert set(rel) == {("warmup", "improves", "acc"), ("warmup", "measured_by", "acc"),
                        ("memory_distillation", "depends_on", "teacher_model")}
    assert rel[("warmup", "improves", "acc")]["evidence"] == ["workflow/notes/meeting.md:3"]
    assert rel[("warmup", "measured_by", "acc")]["source"] == "notes:workflow/notes/table.xlsx#Results!r2"
    assert all(r["approved"] is False for r in world["relations"])

    assert out["status"] == "PASS_WITH_NOTES" and out["skipped"] == ["workflow/notes/photo.png"]
    assert out["dropped"] == 2 and out["repeated"] == 1                      # bad quote, unknown end; LongMemEval
    report = open(out["report"], encoding="utf-8").read()
    assert "`notes:workflow/notes/deck.pptx#slide2`" in report and "Warmup for 1000 steps" in report
    assert "photo.png" in report and "未确认" in report

    # The same notes again add nothing.
    with script_roles({wf.extractor: [reply]}):
        again = json.loads(wf.notes_ingest(str(paper), out_dir=str(paper / "o")))
    assert again["added"] == {"entities": [], "relations": [], "conclusions": []}
    assert json.loads((paper / "workflow" / "world.json").read_text()) == world
    assert again["status"] == "PASS_WITH_NOTES" and again["repeated"] == 8


def test_folder_checks(paper):
    for folder in ("../elsewhere", "workflow/missing"):
        with pytest.raises(ValueError):
            wf.notes_ingest(str(paper), folder=folder)
    (paper / "empty").mkdir()
    (paper / "empty" / "x.bin").write_bytes(b"\0")
    with pytest.raises(ValueError, match="no text"):
        wf.notes_ingest(str(paper), folder="empty")

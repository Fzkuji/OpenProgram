"""Backend operations of the PDF editor example, run against generated PDFs."""

import base64
import importlib.util
import json
import shutil
from io import BytesIO
from pathlib import Path

import pytest

pypdf = pytest.importorskip("pypdf")

APP = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
PAPER = "sample-paper.pdf"
FORM = "forms/registration-form.pdf"
NOTES = "notes/reading-list.pdf"


def _load_backend():
    spec = importlib.util.spec_from_file_location("example_pdf_editor_backend", APP / "backend.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backend = _load_backend()


class Context:
    """The host context: project files, write_file and progress."""

    def __init__(self, project, *, writable=True, instance="test"):
        self.project_path = str(project)
        self.instance_id = instance
        self.events = []
        self.writes = []
        if not writable:
            self.write_file = None

    def progress(self, value):
        self.events.append(value)

    def write_file(self, relative, content):
        root = Path(self.project_path).resolve()
        target = (root / relative).resolve()
        assert target.is_relative_to(root) and ".git" not in target.parts
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        self.writes.append(relative)
        return {"path": target.relative_to(root).as_posix(), "bytes": len(content)}


@pytest.fixture()
def project(tmp_path):
    shutil.copytree(FIXTURES, tmp_path / "project")
    (tmp_path / "project" / "readme.txt").write_text("not a pdf")
    return tmp_path / "project"


@pytest.fixture()
def context(project, tmp_path, monkeypatch):
    monkeypatch.setattr(backend.tempfile, "gettempdir", lambda: str(tmp_path / "stage"))
    return Context(project)


def reader(project, path):
    return pypdf.PdfReader(project / path)


def page_texts(project, path):
    return [page.extract_text() for page in reader(project, path).pages]


# ---------------------------------------------------------------- manifest

def test_manifest_lists_every_operation():
    manifest = json.loads((APP / "application.json").read_text())
    assert manifest["id"] == "example.pdf-editor"
    assert set(manifest["operations"]) == set(backend.operations)
    assert "files.project.write" in manifest["capabilities"]
    agent = {name for name, spec in manifest["operations"].items() if spec.get("agent")}
    assert {"read_text", "find", "summarize", "annotate", "list_annotations", "remove_annotation", "pages", "merge",
            "split", "fill_form", "form_fields"} <= agent
    assert not agent & {"chunk", "upload", "draft_apply", "save", "export"}
    for name, spec in manifest["operations"].items():
        assert len(spec.get("description", "")) > 30, name


# ---------------------------------------------------------------- reading

def test_list_files_finds_pdfs_only(context):
    result = backend.list_files({}, context)
    paths = {item["path"] for item in result["files"]}
    assert paths == {PAPER, NOTES, FORM}
    assert result["project"] == "project"
    assert backend.list_files({"query": "notes"}, context)["files"][0]["path"] == NOTES


def test_info_reports_pages_outline_and_counts(context):
    result = backend.info({"path": PAPER}, context)
    assert result["pages"] == 4
    assert result["revision"]
    assert result["annotations"] == 0
    assert any("Introduction" in item["title"] for item in result["outline"])
    assert all(1 <= item["page"] <= 4 for item in result["outline"] if item["page"])
    assert backend.info({"path": FORM}, context)["form_fields"] == 6


def test_chunk_reassembles_the_file(context, project):
    info = backend.info({"path": PAPER}, context)
    data, offset = b"", 0
    while True:
        part = backend.chunk({"path": PAPER, "offset": offset, "length": 4096, "revision": info["revision"]}, context)
        data += base64.b64decode(part["data"])
        offset += 4096
        if part["done"]:
            break
    assert data == (project / PAPER).read_bytes()


def test_chunk_refuses_a_changed_file(context):
    with pytest.raises(ValueError, match="changed"):
        backend.chunk({"path": PAPER, "revision": "0-0", "offset": 0}, context)


def test_read_text_page_range(context):
    result = backend.read_text({"path": PAPER, "start_page": 2, "end_page": 3}, context)
    assert [page["page"] for page in result["pages"]] == [2, 3]
    assert result["total_pages"] == 4
    assert all(page["text"].strip() for page in result["pages"])


def test_read_text_truncates_at_max_chars(context):
    result = backend.read_text({"path": PAPER, "max_chars": 1000}, context)
    assert result["truncated"]
    assert sum(len(page["text"]) for page in result["pages"]) <= 1000


def test_find_returns_pages_and_snippets(context):
    result = backend.find({"path": PAPER, "query": "SLIDING WINDOW"}, context)
    assert result["total"] >= 2 and 1 in result["pages"]
    assert "sliding window" in result["matches"][0]["snippet"].lower()
    assert backend.find({"path": PAPER, "query": "SLIDING WINDOW", "case_sensitive": True}, context)["total"] == 0
    with pytest.raises(ValueError):
        backend.find({"path": PAPER, "query": "  "}, context)


def test_summarize_pdf_pages(context, monkeypatch):
    prompts = []
    monkeypatch.setattr(backend, "_llm", lambda prompt: prompts.append(prompt) or "  Main claim.\n- point  ")
    result = backend.summarize({"path": PAPER, "start_page": 2, "end_page": 2, "focus": "methods"}, context)
    assert result["summary"] == "Main claim.\n- point"
    assert result["source"]["start_page"] == 2
    assert "[Page 2]" in prompts[0] and "[Page 1]" not in prompts[0]
    assert "Focus on: methods" in prompts[0]
    assert context.events == ["Extracting text", "Summarizing"]


def test_summarize_text(context, monkeypatch):
    monkeypatch.setattr(backend, "_llm", lambda prompt: "ok")
    assert backend.summarize({"text": "Some passage."}, context) == {"summary": "ok", "source": {"kind": "text"}}
    with pytest.raises(ValueError):
        backend.summarize({}, context)


# ---------------------------------------------------------------- annotations

def test_annotate_highlights_a_phrase_and_lists_it(context, project):
    result = backend.annotate({"path": PAPER, "type": "highlight", "text": "recurrent memory", "page": 1,
                               "color": "green", "note": "Key idea"}, context)
    assert result["count"] == 1 and result["added"][0]["page"] == 1
    assert result["added"][0]["text"].lower() == "recurrent memory"
    listed = backend.list_annotations({"path": PAPER}, context)
    assert listed["count"] == 1
    entry = listed["annotations"][0]
    assert entry["type"] == "highlight" and entry["color"] == "#69db7c" and entry["author"] == "Agent"
    assert entry["contents"] == "Key idea"
    assert "recurrent memory" in entry["text"].lower()
    annot = reader(project, PAPER).pages[0]["/Annots"][0].get_object()
    assert annot["/Subtype"] == "/Highlight" and len(annot["/QuadPoints"]) % 8 == 0
    assert "/AP" in annot and annot["/NM"] == entry["id"]


def test_annotate_markup_types_and_all_occurrences(context):
    first = backend.annotate({"path": PAPER, "type": "underline", "text": "sliding window attention", "occurrence": "all"}, context)
    assert first["count"] >= 3
    backend.annotate({"path": PAPER, "type": "strikethrough", "text": "Limitations", "occurrence": 1}, context)
    backend.annotate({"path": PAPER, "type": "squiggly", "text": "benchmarks", "page": 1}, context)
    types = [a["type"] for a in backend.list_annotations({"path": PAPER}, context)["annotations"]]
    assert types.count("underline") == first["count"]
    assert "strikethrough" in types and "squiggly" in types
    only = backend.list_annotations({"path": PAPER, "types": ["strikethrough"]}, context)
    assert only["count"] == 1


def test_annotate_note_shapes_and_text_box(context, project):
    backend.annotate({"path": PAPER, "type": "note", "page": 2, "note": "Check this table"}, context)
    backend.annotate({"path": PAPER, "type": "note", "text": "Table 1", "note": "Numbers look high"}, context)
    backend.annotate({"path": PAPER, "type": "rectangle", "page": 2, "rect": [72, 300, 300, 400], "fill": "#ffeeee"}, context)
    backend.annotate({"path": PAPER, "type": "ellipse", "page": 2, "rect": [320, 300, 420, 360]}, context)
    backend.annotate({"path": PAPER, "type": "line", "page": 3, "points": [[72, 100], [300, 100]]}, context)
    backend.annotate({"path": PAPER, "type": "arrow", "page": 3, "points": [[72, 200], [200, 260]], "width": 3}, context)
    backend.annotate({"path": PAPER, "type": "free_text", "page": 3, "note": "Reviewer comments go here", "rect": [72, 500, 300, 560]}, context)
    listed = backend.list_annotations({"path": PAPER}, context)["annotations"]
    kinds = {a["type"] for a in listed}
    assert kinds == {"note", "rectangle", "ellipse", "line", "arrow", "free_text"}
    notes = [a for a in listed if a["type"] == "note"]
    assert {n["contents"] for n in notes} == {"Check this table", "Numbers look high"}
    pages = reader(project, PAPER).pages
    subtypes = [ref.get_object()["/Subtype"] for page in pages for ref in page.get("/Annots", [])]
    assert subtypes.count("/Popup") == 2  # one per note
    assert all("/AP" in ref.get_object() for page in pages for ref in page.get("/Annots", [])
               if ref.get_object()["/Subtype"] != "/Popup")
    assert [a for a in listed if a["type"] == "free_text"][0]["contents"] == "Reviewer comments go here"


def test_annotate_validates_input(context, project):
    before = (project / PAPER).read_bytes()
    with pytest.raises(ValueError, match="not found"):
        backend.annotate({"path": PAPER, "type": "highlight", "text": "zzqx not in the paper"}, context)
    with pytest.raises(ValueError, match="needs text"):
        backend.annotate({"path": PAPER, "type": "highlight"}, context)
    with pytest.raises(ValueError, match="page"):
        backend.annotate({"path": PAPER, "type": "rectangle", "rect": [0, 0, 10, 10]}, context)
    with pytest.raises(ValueError, match="out of range"):
        backend.annotate({"path": PAPER, "type": "note", "page": 9, "note": "x"}, context)
    with pytest.raises(ValueError, match="color"):
        backend.annotate({"path": PAPER, "type": "highlight", "text": "memory", "color": "chartreuse-ish"}, context)
    with pytest.raises(ValueError, match="type"):
        backend.annotate({"path": PAPER, "type": "scribble", "page": 1}, context)
    with pytest.raises(ValueError, match="occurs"):
        backend.annotate({"path": PAPER, "type": "highlight", "text": "Limitations", "occurrence": 40}, context)
    assert (project / PAPER).read_bytes() == before


def test_annotate_to_output_leaves_source(context, project):
    before = (project / PAPER).read_bytes()
    result = backend.annotate({"path": PAPER, "type": "highlight", "text": "memory", "output": "out/annotated.pdf"}, context)
    assert result["path"] == "out/annotated.pdf"
    assert (project / PAPER).read_bytes() == before
    with pytest.raises(FileExistsError):
        backend.annotate({"path": PAPER, "type": "highlight", "text": "memory", "output": "out/annotated.pdf"}, context)
    backend.annotate({"path": PAPER, "type": "highlight", "text": "memory", "output": "out/annotated.pdf", "overwrite": True}, context)


def test_remove_annotation(context, project):
    backend.annotate({"path": PAPER, "type": "note", "page": 1, "note": "first"}, context)
    backend.annotate({"path": PAPER, "type": "highlight", "text": "memory", "page": 1}, context)
    listed = backend.list_annotations({"path": PAPER}, context)["annotations"]
    note = next(a for a in listed if a["type"] == "note")
    result = backend.remove_annotation({"path": PAPER, "id": note["id"]}, context)
    assert result["removed"] == [note["id"]]
    left = backend.list_annotations({"path": PAPER}, context)["annotations"]
    assert [a["type"] for a in left] == ["highlight"]
    subtypes = [ref.get_object()["/Subtype"] for ref in reader(project, PAPER).pages[0].get("/Annots", [])]
    assert "/Popup" not in subtypes  # the note's popup went with it
    with pytest.raises(ValueError, match="no annotation"):
        backend.remove_annotation({"path": PAPER, "ids": ["nope"]}, context)


def test_ids_stay_stable_across_edits(context):
    backend.annotate({"path": PAPER, "type": "highlight", "text": "memory", "occurrence": "all"}, context)
    ids = [a["id"] for a in backend.list_annotations({"path": PAPER}, context)["annotations"]]
    backend.remove_annotation({"path": PAPER, "ids": [ids[0]]}, context)
    backend.pages({"path": PAPER, "action": "rotate", "pages": [2], "degrees": 90}, context)
    after = [a["id"] for a in backend.list_annotations({"path": PAPER}, context)["annotations"]]
    assert after == ids[1:]


# ---------------------------------------------------------------- pages

def test_pages_rotate_delete_duplicate(context, project):
    texts = page_texts(project, PAPER)
    backend.pages({"path": PAPER, "action": "rotate", "pages": ["1-2"], "degrees": 90}, context)
    assert [p.rotation for p in reader(project, PAPER).pages] == [90, 90, 0, 0]
    backend.pages({"path": PAPER, "action": "rotate", "pages": [1], "degrees": -90}, context)
    assert reader(project, PAPER).pages[0].rotation == 0
    backend.pages({"path": PAPER, "action": "duplicate", "pages": [3]}, context)
    assert page_texts(project, PAPER) == [texts[0], texts[1], texts[2], texts[2], texts[3]]
    backend.pages({"path": PAPER, "action": "delete", "pages": [1, "4-5"]}, context)
    assert page_texts(project, PAPER) == [texts[1], texts[2]]
    with pytest.raises(ValueError, match="every page"):
        backend.pages({"path": PAPER, "action": "delete", "pages": [1, 2]}, context)


def test_pages_move_reorder_and_blank(context, project):
    texts = page_texts(project, PAPER)
    backend.pages({"path": PAPER, "action": "move", "pages": [4], "to": 1}, context)
    assert page_texts(project, PAPER) == [texts[3], texts[0], texts[1], texts[2]]
    backend.pages({"path": PAPER, "action": "reorder", "order": [2, 3, 4, 1]}, context)
    assert page_texts(project, PAPER) == texts
    backend.pages({"path": PAPER, "action": "insert_blank", "after": 0, "size": "letter"}, context)
    backend.pages({"path": PAPER, "action": "insert_blank", "after": 5, "count": 2}, context)
    pages = reader(project, PAPER).pages
    assert len(pages) == 7
    assert (float(pages[0].mediabox.width), float(pages[0].mediabox.height)) == (612.0, 792.0)
    assert not pages[0].extract_text().strip() and not pages[6].extract_text().strip()
    assert round(float(pages[6].mediabox.width)) == round(float(pages[4].mediabox.width))
    with pytest.raises(ValueError, match="exactly once"):
        backend.pages({"path": PAPER, "action": "reorder", "order": [1, 1, 2]}, context)


def test_pages_keep_annotations_and_outline(context, project):
    backend.annotate({"path": PAPER, "type": "highlight", "text": "Limitations"}, context)
    page = backend.list_annotations({"path": PAPER}, context)["annotations"][0]["page"]
    backend.pages({"path": PAPER, "action": "move", "pages": [page], "to": 1}, context)
    listed = backend.list_annotations({"path": PAPER}, context)["annotations"]
    assert [a["page"] for a in listed] == [1]
    titles = [item["title"] for item in backend.info({"path": PAPER}, context)["outline"]]
    assert any("Introduction" in t for t in titles)


def test_pages_delete_prunes_outline(context):
    outline = backend.info({"path": PAPER}, context)["outline"]
    on_last = [o["title"] for o in outline if o["page"] == 4]
    assert on_last
    backend.pages({"path": PAPER, "action": "delete", "pages": [4]}, context)
    after = backend.info({"path": PAPER}, context)["outline"]
    assert not any(o["title"] in on_last for o in after)
    assert all(o["page"] and o["page"] <= 3 for o in after)


def test_pages_extract(context, project):
    texts = page_texts(project, PAPER)
    result = backend.pages({"path": PAPER, "action": "extract", "pages": [3, 1], "output": "parts/extract.pdf"}, context)
    assert result["pages"] == 2 and result["extracted"] == [3, 1]
    assert page_texts(project, "parts/extract.pdf") == [texts[2], texts[0]]
    assert len(page_texts(project, PAPER)) == 4
    with pytest.raises(ValueError, match="new file"):
        backend.pages({"path": PAPER, "action": "extract", "pages": [1], "output": PAPER, "overwrite": True}, context)


def test_pages_validate(context):
    for value, message in [({"action": "rotate", "pages": [1], "degrees": 45}, "degrees"),
                           ({"action": "rotate", "pages": [9]}, "out of range"),
                           ({"action": "rotate", "pages": ["x"]}, "not a page"),
                           ({"action": "move", "pages": [1], "to": 0}, "position"),
                           ({"action": "insert_blank", "after": 99}, "after"),
                           ({"action": "flip"}, "action"),
                           ({"action": "extract", "pages": [1], "output": "../x.pdf"}, "outside")]:
        with pytest.raises((ValueError, FileExistsError), match=message):
            backend.pages({"path": PAPER, **value}, context)


def test_merge_and_split(context, project):
    texts = page_texts(project, PAPER)
    notes = page_texts(project, NOTES)
    backend.merge({"path": PAPER, "sources": [NOTES], "after": 1}, context)
    assert page_texts(project, PAPER) == [texts[0], *notes, *texts[1:]]
    backend.merge({"path": PAPER, "sources": [NOTES, NOTES], "output": "combined.pdf"}, context)
    assert len(page_texts(project, "combined.pdf")) == 5 + 2 * len(notes)
    result = backend.split({"path": PAPER, "every": 2}, context)
    assert [f["path"] for f in result["files"]] == ["sample-paper (part 1).pdf", "sample-paper (part 2).pdf", "sample-paper (part 3).pdf"]
    assert [f["pages"] for f in result["files"]] == [2, 2, 1]
    ranged = backend.split({"path": PAPER, "ranges": ["1-3", "4-end"], "folder": "split", "overwrite": True}, context)
    assert [f["pages"] for f in ranged["files"]] == [3, 2]
    at = backend.split({"path": PAPER, "at": [3], "folder": "cut"}, context)
    assert [f["source_pages"] for f in at["files"]] == [[1, 2], [3, 5]]
    with pytest.raises(FileExistsError):
        backend.split({"path": PAPER, "every": 2}, context)
    with pytest.raises(ValueError, match="every"):
        backend.split({"path": PAPER}, context)
    with pytest.raises((ValueError, FileNotFoundError)):
        backend.merge({"path": PAPER, "sources": ["../outside.pdf"]}, context)


# ---------------------------------------------------------------- forms

def test_form_fields_and_fill(context, project):
    fields = {f["name"]: f for f in backend.form_fields({"path": FORM}, context)["fields"]}
    assert set(fields) == {"full_name", "email", "role", "dietary_notes", "in_person", "session"}
    assert fields["role"]["type"] == "combo" and "Engineer" in fields["role"]["options"]
    assert fields["session"]["type"] == "radio" and set(fields["session"]["options"]) == {"Morning", "Afternoon"}
    assert fields["in_person"]["type"] == "checkbox" and fields["in_person"]["value"] is False
    assert fields["dietary_notes"]["multiline"]
    result = backend.fill_form({"path": FORM, "values": {"full_name": "Ada Lovelace", "in_person": True,
                                                         "session": "Afternoon", "role": "Engineer"}}, context)
    assert {f["name"]: f["value"] for f in result["filled"]} == {
        "full_name": "Ada Lovelace", "in_person": True, "session": "Afternoon", "role": "Engineer"}
    values = reader(project, FORM).get_fields()
    assert values["full_name"]["/V"] == "Ada Lovelace"
    assert values["session"]["/V"] == "/Afternoon"
    widgets = [ref.get_object() for ref in reader(project, FORM).pages[0]["/Annots"]]
    radio_states = [w.get("/AS") for w in widgets if w.get("/Parent") is not None]
    assert "/Afternoon" in radio_states


def test_fill_form_validates(context, project):
    before = (project / FORM).read_bytes()
    for values, message in [({"nope": "x"}, "no field"), ({"session": "Evening"}, "one of"),
                            ({"in_person": "maybe"}, "checkbox"), ({"full_name": ["a"]}, "text"), ({}, "values")]:
        with pytest.raises(ValueError, match=message):
            backend.fill_form({"path": FORM, "values": values}, context)
    assert (project / FORM).read_bytes() == before
    with pytest.raises(ValueError, match="no form fields"):
        backend.fill_form({"path": PAPER, "values": {"a": "b"}}, context)


# ---------------------------------------------------------------- editor drafts

def _upload(context, data, size=5000):
    upload = None
    for offset in range(0, len(data), size):
        final = offset + size >= len(data)
        result = backend.upload({**({"upload": upload} if upload else {}), "offset": offset,
                                 "data": base64.b64encode(data[offset:offset + size]).decode(), "final": final,
                                 "author": "Reviewer"}, context)
        if final:
            return result
        upload = result["upload"]


def _draft_bytes(context, draft):
    data, offset = b"", 0
    while True:
        part = backend.chunk({"draft": draft, "offset": offset}, context)
        data += base64.b64decode(part["data"])
        offset += len(base64.b64decode(part["data"]))
        if part["done"]:
            return data


def test_upload_makes_a_draft_and_names_pdfjs_annotations(context, project):
    # A PDF as PDF.js saves it: an annotation named pdfjs_internal_editor_0 without author.
    writer = pypdf.PdfWriter(clone_from=reader(project, PAPER))
    from pypdf.annotations import FreeText
    annot = FreeText(text="hi", rect=(50, 50, 200, 80))
    annot[pypdf.generic.NameObject("/NM")] = pypdf.generic.TextStringObject("pdfjs_internal_editor_0")
    writer.add_annotation(0, annot)
    out = BytesIO()
    writer.write(out)
    summary = _upload(context, out.getvalue())
    assert summary["draft"].startswith("d_") and summary["pages"] == 4
    entry = summary["annotations"][0]
    assert entry["id"].startswith("op-") and entry["author"] == "Reviewer"
    assert _draft_bytes(context, summary["draft"]).startswith(b"%PDF")
    with pytest.raises(ValueError, match="order"):
        backend.upload({"upload": "u_" + "0" * 32, "offset": 10, "data": ""}, context)


def test_draft_apply_save_and_conflicts(context, project):
    info = backend.info({"path": PAPER}, context)
    first = backend.draft_apply({"path": PAPER, "annotations": [
        {"type": "underline", "page": 1, "quads": [[72, 700, 200, 700, 72, 688, 200, 688]], "color": "#1971c2"},
        {"type": "note", "page": 1, "at": [300, 600], "contents": "Draft note"},
        {"type": "arrow", "page": 2, "points": [[100, 100], [200, 150]], "width": 2},
    ], "author": "Reviewer"}, context)
    assert [a["type"] for a in first["annotations"]] == ["underline", "note", "arrow"]
    assert all(a["author"] == "Reviewer" for a in first["annotations"])
    second = backend.draft_apply({"draft": first["draft"], "sequence": [{"page": 2, "rotate": 90}, {"page": 1}, {"blank": True}],
                                  "remove": [first["annotations"][1]["id"]]}, context)
    assert second["pages"] == 3 and second["draft"] != first["draft"]
    assert {a["type"]: a["page"] for a in second["annotations"]} == {"arrow": 1, "underline": 2}
    assert (project / PAPER).read_bytes() != _draft_bytes(context, second["draft"])
    # Saving checks the revision the editor opened.
    saved = backend.save({"draft": second["draft"], "path": PAPER, "base_revision": info["revision"]}, context)
    assert saved["pages"] == 3 and reader(project, PAPER).pages[0].rotation == 90
    with pytest.raises(ValueError, match="changed on disk"):
        backend.save({"draft": first["draft"], "path": PAPER, "base_revision": info["revision"]}, context)
    backend.save({"draft": first["draft"], "path": PAPER, "base_revision": info["revision"], "overwrite": True}, context)
    assert len(reader(project, PAPER).pages) == 4
    # A copy refuses to replace an existing file unless asked.
    backend.save({"draft": second["draft"], "path": "copies/paper copy.pdf", "as_copy": True}, context)
    with pytest.raises(FileExistsError):
        backend.save({"draft": second["draft"], "path": "copies/paper copy.pdf", "as_copy": True}, context)
    copied = backend.save({"from": NOTES, "path": "copies/notes copy.pdf", "as_copy": True}, context)
    assert copied["pages"] == len(reader(project, NOTES).pages)


def _annot(data, page, index):
    return pypdf.PdfReader(BytesIO(data)).pages[page - 1]["/Annots"][index].get_object()


def _appearance_of(annot):
    return annot["/AP"]["/N"].get_object().get_data().decode("latin-1")


def test_draft_apply_recolours_saved_annotations(context, project):
    # Annotations already saved into the file: a highlight with a note, shapes,
    # an arrow and a text box.
    backend.annotate({"path": PAPER, "type": "highlight", "text": "recurrent memory", "page": 1, "note": "Key idea",
                      "author": "Ada"}, context)
    backend.annotate({"path": PAPER, "type": "rectangle", "page": 2, "rect": [72, 300, 300, 400], "fill": "#ffeeee"}, context)
    backend.annotate({"path": PAPER, "type": "arrow", "page": 2, "points": [[100, 100], [200, 150]], "width": 2}, context)
    backend.annotate({"path": PAPER, "type": "free_text", "page": 3, "note": "Reviewer comments", "rect": [72, 500, 300, 560]}, context)
    backend.annotate({"path": PAPER, "type": "note", "page": 3, "note": "Old text"}, context)
    info = backend.info({"path": PAPER}, context)
    listed = {a["type"]: a for a in backend.list_annotations({"path": PAPER}, context)["annotations"]}
    assert listed["rectangle"]["width"] == 2 and listed["free_text"]["color"] == "#1f1f24"
    highlight = listed["highlight"]

    result = backend.draft_apply({"path": PAPER, "update": [
        {"id": highlight["id"], "color": "blue"},
        {"id": listed["rectangle"]["id"], "color": "#2f9e44", "width": 5},
        {"id": listed["arrow"]["id"], "color": "#1971c2", "width": 4},
        {"id": listed["free_text"]["id"], "color": "#e03131", "contents": "Edited comment"},
        {"id": listed["note"]["id"], "color": "#69db7c", "contents": "New text"},
    ]}, context)
    # Every annotation keeps its id, author, note and covered text; the colour,
    # width and text change in the listing and in the appearance streams.
    assert result["updated"] == {a["id"]: a["id"] for a in listed.values()}
    after = {a["type"]: a for a in result["annotations"]}
    assert set(after) == set(listed)
    assert after["highlight"]["color"] == "#74c0fc"
    assert after["highlight"]["id"] == highlight["id"] and after["highlight"]["author"] == "Ada"
    assert after["highlight"]["contents"] == "Key idea" and after["highlight"]["text"] == highlight["text"]
    assert after["rectangle"]["color"] == "#2f9e44" and after["rectangle"]["width"] == 5
    assert after["arrow"]["color"] == "#1971c2" and after["arrow"]["width"] == 4
    assert after["free_text"]["color"] == "#e03131" and after["free_text"]["contents"] == "Edited comment"
    assert after["note"]["color"] == "#69db7c" and after["note"]["contents"] == "New text"
    data = _draft_bytes(context, result["draft"])
    mark = _annot(data, 1, 0)
    assert "0.455 0.753 0.988 rg" in _appearance_of(mark)
    assert [float(v) for v in mark["/QuadPoints"]] == [float(v) for v in _annot((project / PAPER).read_bytes(), 1, 0)["/QuadPoints"]]
    square = _annot(data, 2, 0)
    assert float(square["/BS"]["/W"]) == 5 and square["/IC"] is not None
    assert "5 w" in _appearance_of(square) and "0.184 0.62 0.267 RG" in _appearance_of(square)
    assert "Edited comment" in _appearance_of(_annot(data, 3, 0)) and "0.878 0.192 0.192 rg" in _annot(data, 3, 0)["/DA"]
    note = _annot(data, 3, 1)
    assert note["/Popup"].get_object()["/Parent"].get_object()["/NM"] == listed["note"]["id"]
    # The file itself changes only when the draft is saved.
    assert backend.list_annotations({"path": PAPER}, context)["annotations"][0]["color"] == highlight["color"]
    backend.save({"draft": result["draft"], "path": PAPER, "base_revision": info["revision"]}, context)
    saved = {a["type"]: a for a in backend.list_annotations({"path": PAPER}, context)["annotations"]}
    assert saved["highlight"]["color"] == "#74c0fc" and saved["rectangle"]["width"] == 5


def test_draft_apply_restyles_unnamed_ink_and_validates(context, project):
    # A pen drawing without /NM, as another viewer may write it: it is
    # addressed by object number and gets a name when restyled.
    from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject
    writer = pypdf.PdfWriter(clone_from=reader(project, PAPER))
    ink = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Ink"),
        NameObject("/Rect"): ArrayObject([FloatObject(v) for v in (90, 90, 210, 160)]),
        NameObject("/InkList"): ArrayObject([ArrayObject([FloatObject(v) for v in (100, 100, 150, 150, 200, 120)])]),
        NameObject("/C"): ArrayObject([FloatObject(1), FloatObject(0), FloatObject(0)]),
        NameObject("/BS"): DictionaryObject({NameObject("/W"): FloatObject(2)}),
    })
    writer.add_annotation(0, ink)
    out = BytesIO()
    writer.write(out)
    (project / PAPER).write_bytes(out.getvalue())
    entry = backend.list_annotations({"path": PAPER}, context)["annotations"][0]
    assert entry["type"] == "ink" and entry["id"].endswith("R") and entry["width"] == 2
    result = backend.draft_apply({"path": PAPER, "update": [{"id": entry["id"], "color": "#1f1f24", "width": 6}]}, context)
    renamed = result["updated"][entry["id"]]
    assert renamed.startswith("op-")
    after = result["annotations"][0]
    assert after["id"] == renamed and after["color"] == "#1f1f24" and after["width"] == 6
    stroke = _appearance_of(_annot(_draft_bytes(context, result["draft"]), 1, 0))
    assert "6 w" in stroke and "100 100 m 150 150 l 200 120 l S" in stroke
    # The new id addresses it from now on.
    again = backend.draft_apply({"draft": result["draft"], "update": [{"id": renamed, "color": "red"}]}, context)
    assert again["updated"] == {renamed: renamed} and again["annotations"][0]["color"] == "#e03131"

    with pytest.raises(ValueError, match="no annotation"):
        backend.draft_apply({"path": PAPER, "update": [{"id": "nope", "color": "red"}]}, context)
    with pytest.raises(ValueError, match="changes nothing"):
        backend.draft_apply({"path": PAPER, "update": [{"id": entry["id"]}]}, context)
    with pytest.raises(ValueError, match="color"):
        backend.draft_apply({"path": PAPER, "update": [{"id": entry["id"], "color": "chartreuse-ish"}]}, context)
    backend.annotate({"path": PAPER, "type": "highlight", "text": "memory", "page": 1}, context)
    mark = next(a for a in backend.list_annotations({"path": PAPER}, context)["annotations"] if a["type"] == "highlight")
    with pytest.raises(ValueError, match="no stroke width"):
        backend.draft_apply({"path": PAPER, "update": [{"id": mark["id"], "width": 3}]}, context)


def test_draft_apply_fill_and_merge_sequence(context, project):
    result = backend.draft_apply({"path": FORM, "fill": {"email": "ada@example.org"}}, context)
    fields = {f["name"]: f["value"] for f in backend.form_fields({"draft": result["draft"]}, context)["fields"]}
    assert fields["email"] == "ada@example.org"
    merged = backend.draft_apply({"path": PAPER, "sequence": [{"page": 1}, {"page": 1, "file": NOTES}, {"page": 1}]}, context)
    assert merged["pages"] == 3
    with pytest.raises(ValueError, match="Nothing"):
        backend.draft_apply({"path": PAPER}, context)
    with pytest.raises(ValueError, match="at least one"):
        backend.draft_apply({"path": PAPER, "sequence": []}, context) if False else backend._sequence(context, b"", [])


def test_export_parts(context, project):
    draft = backend.draft_apply({"path": PAPER, "sequence": [{"page": 4}, {"page": 3}, {"page": 2}, {"page": 1}]}, context)["draft"]
    result = backend.export({"draft": draft, "parts": [{"pages": [1, 2], "output": "a.pdf"}, {"pages": ["3-4"], "output": "b.pdf"}]}, context)
    texts = page_texts(project, PAPER)
    assert page_texts(project, "a.pdf") == [texts[3], texts[2]]
    assert page_texts(project, "b.pdf") == [texts[1], texts[0]]
    with pytest.raises(ValueError, match="twice"):
        backend.export({"path": PAPER, "parts": [{"pages": [1], "output": "c.pdf"}, {"pages": [2], "output": "c.pdf"}]}, context)


def test_drafts_are_per_instance(context, project, tmp_path):
    draft = backend.draft_apply({"path": PAPER, "sequence": [{"page": 1}]}, context)["draft"]
    other = Context(project, instance="someone-else")
    with pytest.raises(FileNotFoundError):
        backend.chunk({"draft": draft, "offset": 0}, other)
    with pytest.raises(ValueError, match="invalid draft"):
        backend.chunk({"draft": "../../etc/passwd", "offset": 0}, context)


# ---------------------------------------------------------------- safety

@pytest.mark.parametrize("path", ["../outside.pdf", "readme.txt", "missing.pdf", "/etc/passwd"])
def test_paths_are_checked(context, path):
    with pytest.raises((ValueError, FileNotFoundError)):
        backend.info({"path": path}, context)
    with pytest.raises((ValueError, FileNotFoundError)):
        backend.annotate({"path": path, "type": "note", "page": 1, "note": "x"}, context)


@pytest.mark.parametrize("output", ["../escape.pdf", "/tmp/escape.pdf", "notes.txt", ".git/x.pdf"])
def test_outputs_are_checked(context, output):
    with pytest.raises(ValueError):
        backend.pages({"path": PAPER, "action": "extract", "pages": [1], "output": output}, context)


def test_writing_needs_the_capability(project, tmp_path, monkeypatch):
    monkeypatch.setattr(backend.tempfile, "gettempdir", lambda: str(tmp_path / "stage"))
    context = Context(project, writable=False)
    with pytest.raises(PermissionError, match="files.project.write"):
        backend.annotate({"path": PAPER, "type": "note", "page": 1, "note": "x"}, context)


def test_no_project_bound():
    with pytest.raises(ValueError, match="project"):
        backend.list_files({}, Context(""))


def test_sequence_rotates_each_use_of_a_page_once(context, project):
    result = backend.draft_apply({"path": PAPER, "sequence": [{"page": 2, "rotate": 90}, {"page": 2, "rotate": 90}, {"page": 2}]}, context)
    pages = pypdf.PdfReader(BytesIO(_draft_bytes(context, result["draft"]))).pages
    assert [page.rotation for page in pages] == [90, 90, 0]

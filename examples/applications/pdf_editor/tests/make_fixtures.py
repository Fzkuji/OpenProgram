"""Regenerate the sample PDFs: two documents with headless Chromium
(Playwright) and a fillable form with pypdf.

    python examples/applications/pdf_editor/tests/make_fixtures.py
"""
from pathlib import Path

HERE = Path(__file__).parent / "fixtures"

SECTIONS = [
    ("1 Introduction", [
        "Long documents challenge transformer models because full attention grows quadratically with sequence length. "
        "Practitioners therefore truncate inputs, which discards evidence that later questions depend on.",
        "We study sliding window attention with a small recurrent memory. Each layer attends to a fixed window of "
        "recent tokens and to a compressed memory carried across windows.",
        "Our contributions are threefold: a training recipe that keeps the window small, an analysis of what the "
        "memory stores, and an evaluation on three long-document benchmarks.",
    ]),
    ("2 Related Work", [
        "Sparse attention patterns reduce cost by restricting which tokens interact. Recurrent memories, by contrast, "
        "summarise the past into a fixed number of slots.",
        "Retrieval-augmented models fetch passages from an external index; they are complementary to our approach.",
    ]),
    ("3 Method", [
        "Given a sequence split into windows of w tokens, layer l computes attention over the current window and the "
        "memory state m from the previous window. The memory is updated with a gated write.",
    ]),
    ("3.1 Memory update", [
        "The gate decides how much of each memory slot to overwrite. We initialise the gate bias so that the memory "
        "is mostly preserved early in training.",
    ]),
    ("3.2 Training objective", [
        "We train with the standard language-modelling loss and an auxiliary reconstruction loss on the memory.",
    ]),
    ("4 Experiments", [
        "We evaluate on three benchmarks of long documents: question answering, summarisation and retrieval. "
        "Sliding window attention with memory matches full attention within one point while running 2.1 times faster.",
        "Table 1 reports accuracy and throughput. Ablations show the memory matters most for questions whose evidence "
        "lies more than four windows back.",
    ]),
    ("5 Limitations", [
        "All corpora are English. We did not ablate the window size beyond three values, and the memory size is fixed.",
    ]),
    ("6 Conclusion", [
        "A small recurrent memory lets sliding window attention keep long-range evidence at a fraction of the cost of "
        "full attention.",
    ]),
]


FILLER = [
    "The effect holds across model sizes from 125M to 1.3B parameters.",
    "We keep the tokenizer and optimiser settings of the baseline unchanged.",
    "Throughput is measured on a single accelerator with a batch of eight documents.",
    "Each result is the mean of three seeds; standard deviations are below 0.4 points.",
    "Shorter windows lower memory use but hurt questions whose evidence is far away.",
    "The memory has 64 slots of the model dimension and is reset at document boundaries.",
    "Gradients flow through the memory for two windows and are truncated beyond that.",
    "Inspection of the memory shows that it keeps entity names and numbers most often.",
    "We release the training configuration so that the results can be reproduced.",
    "Documents longer than 32k tokens are rare in the benchmarks and are left out.",
    "The auxiliary loss weight is 0.1 in all runs; larger weights slow convergence.",
    "At inference time the memory adds less than three percent to the latency.",
]


def html():
    parts = ["<h1 class='title'>Sliding Window Attention with Recurrent Memory</h1>",
             "<p class='authors'>A. Researcher · B. Scientist</p>",
             "<p class='abstract'><b>Abstract.</b> We show that sliding window attention combined with a compact "
             "recurrent memory keeps long-context quality while reducing cost. On three long-document benchmarks it "
             "matches full attention within one point at 2.1× the speed.</p>"]
    for index, (title, paragraphs) in enumerate(SECTIONS):
        tag = "h3" if "." in title.split()[0] else "h2"
        breaks = " style='break-before: page'" if index in (2, 5) else ""
        parts.append(f"<{tag}{breaks}>{title}</{tag}>")
        for number, paragraph in enumerate(paragraphs):
            parts.append(f"<p>{paragraph}</p>")
            extra = FILLER[(index * 3 + number) % len(FILLER):] + FILLER[:(index * 3 + number) % len(FILLER)]
            parts.append(f"<p>{' '.join(extra[:4])}</p>")
        if title.startswith("4"):
            parts.append("<table><tr><th>Model</th><th>QA</th><th>Summ.</th><th>Tokens/s</th></tr>"
                         "<tr><td>Full attention</td><td>71.2</td><td>38.4</td><td>1.0×</td></tr>"
                         "<tr><td>Sliding window</td><td>64.0</td><td>35.1</td><td>2.4×</td></tr>"
                         "<tr><td>Ours</td><td>70.5</td><td>38.0</td><td>2.1×</td></tr></table>")
    css = """body{font:11pt/1.5 Georgia,serif;margin:0 18mm;color:#111}
    .title{font-size:20pt;text-align:center;margin:12mm 0 2mm}.authors{text-align:center;color:#444}
    .abstract{margin:8mm 10mm;font-size:10pt}h2{font-size:13pt;margin-top:8mm}h3{font-size:11.5pt}
    table{border-collapse:collapse;margin:4mm auto}td,th{border-bottom:1px solid #999;padding:2mm 4mm}"""
    return f"<html><head><title>Sliding Window Attention with Recurrent Memory</title><style>{css}</style></head><body>{''.join(parts)}</body></html>"


def make_form(path):
    """A one-page registration form: text, multiline, combo, checkbox and radio fields."""
    from pypdf import PdfWriter
    from pypdf.generic import (ArrayObject, BooleanObject, DecodedStreamObject, DictionaryObject, FloatObject,
                               NameObject, NumberObject, TextStringObject)
    writer = PdfWriter()
    page = writer.add_blank_page(612, 792)
    helv = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"), NameObject("/Encoding"): NameObject("/WinAnsiEncoding")}))
    zapf = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/ZapfDingbats")}))
    fonts = DictionaryObject({NameObject("/Helv"): helv, NameObject("/ZaDb"): zapf})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): fonts})
    labels = [(72, 700, 22, "Workshop registration"), (72, 676, 10.5, "Long-context models reading group, spring session."),
              (72, 630, 11, "Full name"), (72, 580, 11, "Email"), (72, 530, 11, "Role"),
              (72, 480, 11, "Session"), (110, 457, 11, "Morning"), (230, 457, 11, "Afternoon"),
              (72, 420, 11, "Dietary notes"), (98, 333, 11, "I will attend in person")]
    text = "".join(f"BT /Helv {size} Tf 0.12 0.12 0.14 rg {x} {y} Td ({t}) Tj ET\n" for x, y, size, t in labels)
    text += "0.85 0.85 0.88 RG 0.8 w 72 662 m 540 662 l S\n"
    content = DecodedStreamObject()
    content.set_data(text.encode("latin-1"))
    page[NameObject("/Contents")] = writer._add_object(content)

    def stream(data, box):
        s = DecodedStreamObject()
        s.set_data(data.encode("latin-1"))
        s.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"),
                  NameObject("/BBox"): ArrayObject([FloatObject(v) for v in box]),
                  NameObject("/Resources"): DictionaryObject({NameObject("/Font"): fonts})})
        return writer._add_object(s)

    def widget(rect, **extra):
        d = DictionaryObject({NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Widget"),
                              NameObject("/Rect"): ArrayObject([FloatObject(v) for v in rect]),
                              NameObject("/F"): NumberObject(4), NameObject("/P"): page.indirect_reference,
                              NameObject("/MK"): DictionaryObject({
                                  NameObject("/BC"): ArrayObject([FloatObject(0.6)] * 3),
                                  NameObject("/BG"): ArrayObject([FloatObject(1)] * 3)})})
        for key, value in extra.items():
            d[NameObject("/" + key)] = value
        return d

    fields, annots = ArrayObject(), ArrayObject()

    def add(d):
        ref = writer._add_object(d)
        fields.append(ref)
        annots.append(ref)
        return ref

    da = TextStringObject("/Helv 11 Tf 0 g")
    add(widget([72, 600, 400, 622], FT=NameObject("/Tx"), T=TextStringObject("full_name"),
               TU=TextStringObject("Full name"), DA=da, V=TextStringObject("")))
    add(widget([72, 550, 400, 572], FT=NameObject("/Tx"), T=TextStringObject("email"),
               TU=TextStringObject("Email"), DA=da, V=TextStringObject("")))
    add(widget([72, 500, 260, 522], FT=NameObject("/Ch"), T=TextStringObject("role"), TU=TextStringObject("Role"),
               Ff=NumberObject(1 << 17), DA=da, V=TextStringObject("Researcher"),
               Opt=ArrayObject([TextStringObject(o) for o in ("Researcher", "Engineer", "Student", "Other")])))
    add(widget([72, 345, 540, 412], FT=NameObject("/Tx"), T=TextStringObject("dietary_notes"),
               TU=TextStringObject("Dietary notes"), Ff=NumberObject(1 << 12), DA=da, V=TextStringObject("")))
    box = [0, 0, 14, 14]
    on = stream("0 g BT /ZaDb 11 Tf 2 3 Td (4) Tj ET", box)
    off = stream("", box)
    add(widget([76, 330, 90, 344], FT=NameObject("/Btn"), T=TextStringObject("in_person"),
               TU=TextStringObject("I will attend in person"), V=NameObject("/Off"), AS=NameObject("/Off"),
               DA=TextStringObject("/ZaDb 11 Tf 0 g"),
               AP=DictionaryObject({NameObject("/N"): DictionaryObject({NameObject("/Yes"): on, NameObject("/Off"): off})})))
    radio = DictionaryObject({NameObject("/FT"): NameObject("/Btn"), NameObject("/T"): TextStringObject("session"),
                              NameObject("/TU"): TextStringObject("Session"),
                              NameObject("/Ff"): NumberObject((1 << 15) | (1 << 14)), NameObject("/V"): NameObject("/Off")})
    radio_ref = writer._add_object(radio)
    kids = ArrayObject()
    for x, state in ((92, "Morning"), (212, "Afternoon")):
        dot = stream("0 g 10.5 7 m 10.5 8.93 8.93 10.5 7 10.5 c 5.07 10.5 3.5 8.93 3.5 7 c 3.5 5.07 5.07 3.5 7 3.5 c 8.93 3.5 10.5 5.07 10.5 7 c f", [0, 0, 14, 14])
        kid = widget([x, 455, x + 14, 469], AS=NameObject("/Off"), Parent=radio_ref,
                     AP=DictionaryObject({NameObject("/N"): DictionaryObject({NameObject("/" + state): dot, NameObject("/Off"): off})}))
        kid_ref = writer._add_object(kid)
        kids.append(kid_ref)
        annots.append(kid_ref)
    radio[NameObject("/Kids")] = kids
    fields.append(radio_ref)
    page[NameObject("/Annots")] = annots
    writer._root_object[NameObject("/AcroForm")] = DictionaryObject({
        NameObject("/Fields"): fields, NameObject("/DA"): da, NameObject("/NeedAppearances"): BooleanObject(True),
        NameObject("/DR"): DictionaryObject({NameObject("/Font"): fonts})})
    writer.add_metadata({"/Title": "Workshop registration"})
    with open(path, "wb") as handle:
        writer.write(handle)


def main():
    HERE.mkdir(parents=True, exist_ok=True)
    (HERE / "forms").mkdir(exist_ok=True)
    make_form(HERE / "forms" / "registration-form.pdf")
    from playwright.sync_api import sync_playwright
    (HERE / "notes").mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html())
        page.pdf(path=str(HERE / "sample-paper.pdf"), format="A4", outline=True, tagged=True,
                 margin={"top": "18mm", "bottom": "18mm"})
        page.set_content("<html><head><title>Reading list</title></head><body style='font:12pt sans-serif;margin:20mm'>"
                         "<h1>Reading list</h1><ol><li>Sliding window attention</li><li>Recurrent memory</li>"
                         "<li>Retrieval augmentation</li></ol></body></html>")
        page.pdf(path=str(HERE / "notes" / "reading-list.pdf"), format="A5")
        browser.close()


if __name__ == "__main__":
    main()

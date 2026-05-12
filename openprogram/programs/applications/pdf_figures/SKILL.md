---
name: pdf-figures
description: "Extract figures from academic PDFs by anchoring on caption prefixes (e.g. 'Figure 2:'). Pure Python, no LLM. Triggers: 'extract figures', 'pdf figures', 'crop figures from paper', '抽取论文图', '提取PDF配图', 'figure from arxiv'."
---

# PDF Figure Extraction

Caption-anchored figure cropping for academic PDFs. Pure pymupdf — no neural
network, no JVM. Built for NeurIPS / arXiv-style layouts with ~10pt body text.

## Usage

```
/pdf-figures "<pdf_path> <caption_prefix> <output_path>"
```

Single entry — the agent reads your request, picks the right entry function
(`extract_pdf_figures` for batch, `extract_one_figure` for single), and runs it.

```
/pdf-figures "Crop Figure 2 from paper.pdf to fig2.png"
/pdf-figures "Extract all 9 figures from /path/to/NeurIPS.pdf into /tmp/figs/"
/pdf-figures "把这篇论文里的所有 Figure 抽出来到 wiki/figures/"
```

## Python

```python
from openprogram.programs.applications.pdf_figures import (
    extract_pdf_figures, extract_one_figure,
)

# Batch
extract_pdf_figures(
    pdf_path="paper.pdf",
    captions=(
        "Figure 1: => fig1.png\n"
        "Figure 2: => fig2.png\n"
        "Figure 7: => fig7.png\n"
    ),
    out_dir="figures/",
    page_hints="Figure 7: => 17",
)

# Single figure
result = extract_one_figure(
    pdf_path="paper.pdf",
    caption_prefix="Figure 2:",
    out_path="figures/fig2.png",
    page_hint=4,
)
print(result.page, result.bbox, result.image_path)
```

## Available Functions

The entry function `extract_pdf_figures` handles batch extraction in one call.
Underneath it dispatches to `extract_one_figure` per caption. Both are
deterministic — they accept a `runtime` parameter for application-protocol
consistency but never invoke an LLM.

The full algorithm and tunable parameters are documented in the docstring of
`extract_one_figure` in `extract.py`. Adding new capabilities should be done
by extending that docstring + the underlying function, not by creating a
sibling skill.

## Known Limitations

- **Wrapfigure layouts** (body text flowing around an inset figure) produce
  undersized crops because the above-caption search stops at the wrap-side
  body paragraph.
- **Atypical text sizes** — thresholds tuned for ~10pt body text. Very small
  or very large body fonts may need parameter adjustment.
- **Caption fused with prose** — if PyMuPDF emits the caption glued to
  surrounding text in a single block, the prefix anchor can fail.

For the PDFFigures2-style "body-text-absence" approach (more general but
also more failure modes on edge cases), see the experimental module at
`openprogram.tools.pdf.figures`. For an industrial-grade alternative,
shell out to Allen AI's Scala `pdffigures2.jar` directly.

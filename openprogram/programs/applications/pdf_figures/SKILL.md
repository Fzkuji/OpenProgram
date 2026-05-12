---
name: pdf-figures
description: "Extract figures from academic PDFs with VLM verification — heuristic crop first, then agent inspects each result and re-renders bad ones until correct. Triggers: 'extract figures', 'crop figures from PDF', 'pdf figure', '抽取论文图', '提取PDF配图', 'figures from arxiv', 'auto crop paper figures'."
---

# PDF Figure Extraction (VLM-verified)

Two-pass figure extraction for academic PDFs, both layers inside
this application:

1. **Heuristic pass** (`_heuristic.py`, private to this application):
   pure pymupdf, caption-anchored cropping. Fast first guess.
2. **VLM verification pass** (`main.py`): for each candidate crop,
   the agent looks at the crop + the full page and decides whether
   the crop is correctly bounded. If not, the agent returns a
   corrected bbox in PDF points and we re-render. Loops up to
   `max_retries` times per figure.

The caller doesn't inspect intermediate results or tweak parameters.

## Usage

```
/pdf-figures "<pdf_path> -> <out_dir>"
```

Or programmatically:

```python
from openprogram.programs.applications.pdf_figures import (
    extract_pdf_figures,
)

results = extract_pdf_figures(
    pdf_path="paper.pdf",
    out_dir="figures/",
    max_retries=3,
    runtime=runtime,  # injected automatically by @agentic_function
)
for r in results:
    print(r["label"], r["page"], r["retries_used"], r["verified"])
```

## When to use

- New / unknown PDF where you can't predict whether the heuristic
  will get it right.
- Production pipelines that need ≈100% correct crops without manual
  review.

## When NOT to use

- Bulk processing 1000s of PDFs where VLM cost matters — import
  the private heuristic helpers directly (`_heuristic.py`) and skip
  the verification loop. Loses self-healing.
- PDFs where you've verified the heuristic already works (same
  template as a paper you've extracted before).

## Available Functions

Single public entry:

- `extract_pdf_figures(pdf_path, out_dir, runtime, ...)` —
  one-shot extraction with VLM verification loop. The function's
  docstring lists all options.

`_heuristic.py` is private to this application but exposes the raw
deterministic helpers (`extract_all_figures`, `extract_one_figure`,
`extract_with_bbox`, `list_captions`, `render_full_page`) for
advanced callers that want to bypass the LLM step.

## Runtime Requirements

Must use a VLM-capable provider (Gemini 2.5 Pro, Claude Sonnet,
GPT-4o or newer). Text-only models will fail verification because
they can't see the candidate crop.

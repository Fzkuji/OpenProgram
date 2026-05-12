---
name: pdf-figures
description: "Extract figures from academic PDFs with VLM verification — heuristic crop first, then agent inspects each result and re-renders bad ones until correct. Triggers: 'extract figures', 'crop figures from PDF', 'pdf figure', '抽取论文图', '提取PDF配图', 'figures from arxiv', 'auto crop paper figures'."
---

# PDF Figure Extraction (VLM-verified)

Two-pass figure extraction for academic PDFs:

1. **Heuristic pass** (`openprogram.tools.pdf.extract_figure`): pure
   pymupdf, caption-anchored cropping. Fast, ~80% accurate on
   standard NeurIPS / arXiv layouts.
2. **VLM verification pass** (this application): for each candidate
   crop, the agent looks at the crop + the full page and decides
   whether the crop is correctly bounded. If not, the agent returns
   a corrected bbox in PDF points and the tool re-renders. Loops up
   to `max_retries` times per figure.

The caller doesn't inspect intermediate results or tweak parameters.

## Usage

```
/pdf-figures "<pdf_path> -> <out_dir>"
```

Or programmatically:

```python
from openprogram.programs.applications.pdf_figures import (
    extract_pdf_figures_verified,
)

results = extract_pdf_figures_verified(
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

- Bulk processing 1000s of PDFs where VLM cost matters — use the
  raw `openprogram.tools.pdf.extract_figure.extract_all_figures`
  instead (no LLM call, deterministic).
- PDFs where you've verified the heuristic already works (same
  template as a paper you've extracted before).

## Available Functions

- `extract_pdf_figures_verified(pdf_path, out_dir, runtime, ...)` —
  the entry point.

Underlying primitives in `openprogram.tools.pdf.extract_figure`
(deterministic, no LLM):
- `extract_all_figures` — one-shot discovery + heuristic crop
- `extract_one_figure` — single caption-anchored crop
- `extract_with_bbox` — render an explicit bbox (used by the retry loop)
- `list_captions` — discovery only, no render
- `render_full_page` — for VLM context

## Runtime Requirements

Must use a VLM-capable provider (Gemini 2.5 Pro, Claude Sonnet,
GPT-4o or newer). Text-only models will fail verification because
they can't see the candidate crop.

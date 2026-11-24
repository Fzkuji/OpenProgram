# Step 1: prepare

Copies the baseline (`git archive`) and the draft into a temporary folder, builds both, and reads everything the checks need into one `Paper` object. This is the only step that runs external tools.

| File | Contents |
| --- | --- |
| `__init__.py` | `Paper` and `prepare()`: builds the draft, the appendix version and the baseline, renders the comparison PDF, copies the PDFs to the report folder. |
| `build.py` | `latexmk`, `git archive`, `latexdiff` + `lualatex` with `assets/diff_preamble.tex` (green added, red struck-through removed). |
| `pdftext.py` | Poppler: page text, line and word boxes, Type 3 fonts. |
| `source.py` | LaTeX parsing: flatten `\input`, floats, first mentions, paragraphs with their section, sentences. |

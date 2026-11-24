# Step 2: checks

Each check takes the prepared `Paper` and returns findings `{level, check, message}`. `FAIL` blocks the change; `WARN` must be read. Checks run no tools and call no model.

| File | Checks |
| --- | --- |
| `output.py` | Build log (overfull, undefined references), body page limit, appendix build, Type 3 fonts, comparison PDF. |
| `venue.py` | Camera-ready lines, required section, authors, numbered section files, code link, comment commands. |
| `floats.py` | Top placement, order of first mention, captions, caption position, vector graphics, removed originals, booktabs, same page as first mention, text size in figures. |
| `text.py` | Wording and expression rules on the paragraphs under review, new numbers, deleted paragraphs, placeholders, absolute words, SOTA, bold, headings, last line of paragraphs, list items and captions (four words and half a line, else FAIL). |
| `latex.py` | Spaces after brackets, quotes, e.g., and i.e., inline and display math, itemize. |
| `citations.py` | `Figure~\ref` form, label prefixes, `~\citep`, citation as subject, cite keys, arXiv and duplicate bib entries, unchanged `.bib`. |
| `base.py` | `finding()` and the prose view of the source. |

`__init__.py` lists every check in `CHECKS`; a new check goes into its file's `CHECKS` list.

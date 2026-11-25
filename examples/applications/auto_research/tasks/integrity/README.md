# integrity tools

Three plain-code checks for research-integrity problems a venue or a reader would catch after submission (`tasks/integrity/`). None makes a model call. Each takes the project's Git repository as `paper_dir` (anything else raises `ValueError`), writes into `<out_dir>/<timestamp>/` with a trail (`run.json`, `run.md`), and returns JSON with `status`, `report` and `run`. Findings are `{kind, level, where, problem, fix, text}`, problem and fix in Simplified Chinese; the main `.tex` and the files it uses come from `tasks/submission/common.py`.

Learned from PhantomLint and hiddenprompt-scan (hidden prompt injection in PDFs, treated as academic misconduct at ICML), infini-gram contamination checks, and Proofig / ImageTwin (image reuse).

## hidden_text_check

| Item | Contract |
| --- | --- |
| Signature | `hidden_text_check(paper_dir, out_dir="")` |
| Purpose | Find text a human reader cannot see but a model reading the PDF or the source does, above all hidden instructions to LLM reviewers. |
| Input | `<main>.pdf` next to the main `.tex`, and every `.tex` the main file inputs. `out_dir` defaults to `<paper_dir>/drafts/hidden_text_check`. |
| PDF | Text whose fill colour is within RGB distance 30 of white, whose effective font size is under 2pt, whose origin lies outside the crop box, drawn in render mode 3 or 7, or with fill opacity 0. Backend, first available: PyMuPDF (`get_texttrace`; white text over a coloured drawing is not counted), pdfminer.six (render mode and opacity from the stream scan), else a content-stream interpreter written here (pages, fonts with ToUnicode and ExtGState read with pypdf when importable, else from the raw file with Flate decoding) plus `pdftotext -bbox-layout` for tiny words. The JSON names the backend used. |
| Source | `\color`/`\textcolor` white (also `[rgb]{1,1,1}`, `[gray]{1}`, `white!9x`, colours defined as white), `\fontsize` under 2pt, `\phantom` holding `\tiny`, five or more words or an instruction, `\resizebox`/`\scalebox` to 0.0x, `\transparent{0}`; each with `file:line` and its text. Instruction-like phrases (ignore previous/prior instructions, give a positive review, as a reviewer you must, LLM/AI reviewer, recommend acceptance, do not mention weaknesses; Chinese 忽略之前的指令, 给出正面评价, 作为审稿人你必须, 推荐接收, 不要提及缺点) anywhere in the text or in comments. |
| Output | `FAIL`: hidden text (PDF or source) that reads like an instruction. `PASS_WITH_NOTES`: other hidden text, visible instruction-like phrases (a paper about prompt injection quotes them), phrases in comments (they ship in an arXiv source bundle), or no PDF. JSON also has `errors`, `notes`, `backend`. Files: `report.md` (Chinese), `hidden_text.json` (findings with page, reasons, position, size). |
| Effects | Runs `pdftotext` when that backend is used. Writes only the report folder. |

## contamination_check

| Item | Contract |
| --- | --- |
| Signature | `contamination_check(paper_dir, data, field="", n="13", sample="200", index="v4_rpj_llama_s4", out_dir="")` |
| Purpose | For a benchmark paper: how many test items already occur in public pretraining corpora. |
| Input | `data`: `.jsonl`, `.json` (a list, or the longest list in an object), `.csv` or `.tsv`, relative to `paper_dir` or absolute; a missing file raises `ValueError`. `field`: the text column (dotted path allowed; a list value is joined); otherwise the column with the longest strings. `index`: comma-separated infini-gram index names. `out_dir` defaults to `<paper_dir>/drafts/contamination_check`. |
| Process | `sample` items drawn with a fixed seed; up to three windows of `n` whitespace tokens per item (start, middle, end; a shorter item is one window); each window counted with `POST https://api.infini-gram.io/ {"index", "query_type": "count", "query"}`. Calls are paced (0.5 s, longer on retry, two retries) and capped at `sample*3` per index; an item stops at its first hit; five failures in a row stop that index. An item is contaminated in an index when any window count is above 0. |
| Output | `FAIL`: more than 10% of the checked items contaminated in any index. `PASS_WITH_NOTES`: any contamination, or items left unchecked. JSON also has `field`, `sampled`, `indexes` and `summary` per index (`checked`, `contaminated`, `rate`, `ci95` Wilson interval, `unchecked`, `stopped`). Files: `report.md` (Chinese), `contamination.json` (per-item windows and counts), `known_contamination.md` (an English "Known contamination" paragraph for the datasheet's limitations, e.g. `rai:dataLimitations` in `croissant_draft`). |
| Effects | Network calls to api.infini-gram.io only. Writes only the report folder. |

## image_reuse

| Item | Contract |
| --- | --- |
| Signature | `image_reuse(paper_dir, own_papers="", out_dir="")` |
| Purpose | Catch a figure reused from the author's earlier paper without credit, and duplicated figures inside the paper. |
| Input | The PNG/JPG/PDF files the main `.tex` includes (PDF figures rasterized with `pdftoppm -png -r 72 -singlefile`). `own_papers`: comma-separated folders or files of images or PDFs (PDF images extracted with `pdfimages -png`), relative to `paper_dir` or absolute, plus `workflow/own_papers/`; a missing path raises `ValueError`. `out_dir` defaults to `<paper_dir>/drafts/image_reuse`. |
| Process | Average hash (8x8) and difference hash (9x8) computed here from Pillow thumbnails, transparency flattened on white; near-uniform images are skipped. A pair matches when both hashes differ in at most 6 of 64 bits. A match is credited when its figure's caption says reproduced/adapted/reprinted from, cites with `from \cite{...}`, or says 引自/改编自/转载自. |
| Output | `FAIL`: an uncredited match with an earlier paper. `PASS_WITH_NOTES`: credited reuse, duplicates inside the paper, unreadable or skipped (EPS/SVG) figures, or Pillow missing (`supported: false`, nothing compared). JSON also has `supported`, `figures`, `own_images`, `matches`, `tools`. Files: `report.md` (Chinese), `image_reuse.json` (hashes, matches with both paths, distances, caption and `file:line` of the figure). |
| Effects | Runs `pdftoppm` and `pdfimages` when on PATH. Writes the report folder and the extracted images under it. |

## Tests

`tests/integrity/`: a one-page PDF with white, invisible (`3 Tr`), 1pt and off-page text (reportlab when importable, else a handwritten content stream, also Flate-compressed for the raw scan), LaTeX fixtures with each source trick, a fake infini-gram `post`, image matching with a fake hash reader, and real Pillow-generated images (skipped without Pillow).

## Usage

`from auto_research.tasks.integrity import hidden_text_check, contamination_check, image_reuse`. In the app as 隐藏文字检查, 基准污染检查 and 图片复用检查.

## Not covered

- White text is judged against white, not the colour actually behind it, except with PyMuPDF (filled drawings under the text); white text on a dark box is a note.
- Text inside form XObjects in the stream-scan backend; glyph widths are not used, so positions are each string's origin.
- Contamination by paraphrase or translation, corpora without an infini-gram index, and tokenization differences between whitespace windows and the index tokenizer.
- Reuse of a cropped, rotated or recoloured panel, panels inside one image file, and vector figures in earlier papers (pdfimages extracts only raster images).

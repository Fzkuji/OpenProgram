# paper_proofread task

The `paper_proofread` task of auto_research (`tasks/proofread/`). Call `paper_proofread()` on the working tree, or on `draft_dir`; the reports go to `drafts/paper_proofread/`.

Proofreads the paper section by section, the way Overleaf's citation reviewer, Writefull, Paperpal and OpenAI Prism check a manuscript and the AAAI-26 AI-review pilot and paperreview.ai check its claims: math, notation, numbers, statistics, missing citations and language. Every issue quotes the text it is about, and code drops a quote that is not in the section. It also proposes titles and abstracts. paper_review judges the paper as a submission; this task finds the errors a careful co-author would mark.

| Item | Contract |
| --- | --- |
| Purpose | Errors to fix before submission, each with its exact quote, and exact edits an app can apply one at a time. |
| Input | `paper_dir`: the paper's Git repository; `workflow/rules.json` names `main_tex` and `bib_files`. `draft_dir`: a copy to proofread instead; empty reads the working tree. `out_dir`: the report folder (default `<paper_dir>/drafts/paper_proofread`). A missing repository or main file, or a main file with no text, raises `ValueError`. |
| Sections | The main file with its `\input` files inlined and comments removed, from `\begin{document}` to the bibliography (`../revision/steps/prepare/source.py`), cut at each `\section`. Text before the first section (the abstract) is the section `Abstract`. |
| Issue kinds | `math`: a step that does not follow, or wrong dimensions. `notation`: a symbol used before it is defined, or defined twice differently. `number`: numbers that disagree across text and tables, or with arithmetic. `statistics`: a significance or comparison claim without the test or variance it needs. `missing_citation`: a claim about prior work or the field with no `\cite`, with one or two search queries. `language`: grammar, a wrong word, an unclear sentence. `language` and `notation` carry a replacement for the quote. |
| Output | JSON with `status`, `report`, `proofread`, `edits`, `run`, the count per kind, `dropped_quotes` and `unavailable_sources`. `FAIL`: any `math` or `number` issue. `PASS_WITH_NOTES`: any other issue, or a search source failed. `PASS`: neither. Files in `<out_dir>/<timestamp>/`: `report.md` (Chinese, a count table first, then issues grouped by kind, then titles and abstracts), `proofread.json` (`status`, `draft`, `counts`, `dropped_quotes`, `unavailable_sources`, `issues` with `section`, `kind`, `quote`, `problem`, `replacement`, `queries`, `candidates`, `edit`; `titles`; `abstracts`), `edits.json` (`root` and `edits`, each with `id`, `file`, `section`, `kind`, `quote`, `replacement`, `problem`), `run.json`, `run.md`. |
| Edits | Only a `language` or `notation` issue whose quote occurs exactly once in exactly one `.tex` file under the proofread folder becomes an edit; `file` is relative to `root`. `apply_edit(paper_dir, edit) -> bool` replaces the quote in that file only when it still occurs there exactly once, and returns `False` otherwise (also for a file outside `paper_dir` or not `.tex`). |
| Citations | Each missing-citation issue's queries run in arXiv, Semantic Scholar and OpenAlex (`../survey/search.py`) for the last fifteen years, five records per query and source. Up to three candidates are kept, with title, year, venue, URL and a key (`lastname+year+firstword`); a candidate whose normalized title is in the paper's `.bib` files gets that entry's key, `in_bib: true` and the note `already in bib — cite it`, and comes first. |
| Effects | Reads the paper and its `.bib` files; sends the citation queries to the public search APIs. Writes only the report folder; the workflow never modifies the paper sources. Only `apply_edit`, called by the author or an app, changes a `.tex` file. |
| Model calls | `checker`: one call per section with the earlier sections for reference (`proofread_issues` schema, at most 25 issues). `titler`: one call with the current title, abstract and introduction: three titles and two abstracts in English, each abstract at most 250 words (or the paper's own abstract length when longer), each with a one-sentence Chinese note on what it emphasizes (`title_proposals` schema). |
| Failure | A malformed reply, an unknown kind, an issue without a problem, a `language`/`notation` issue without a replacement, a missing citation without a query, or a titler reply without three titles and two abstracts within the word limit raises. A quote not found verbatim in its section is dropped and counted, not raised. A source that fails returns nothing and is named in the report. Rerunning is safe. |
| Tests | `tests/proofread/test_workflow.py`: the entry with the search replaced and scripted roles; `apply_edit`. |
| Usage | `from auto_research.tasks.proofread import paper_proofread, apply_edit` |

## Process

1. **Read** (code). Flatten the main file, cut it into sections, read the titles of the `.bib` entries.
2. **Check.** The `checker` reads one section at a time. Code keeps an issue only when its quote occurs verbatim in that section.
3. **Search** (code). Missing-citation queries run in every source; the closest records are kept and those already in the `.bib` are marked.
4. **Titles.** The `titler` proposes three titles and two abstracts.
5. **Report** (code). The status, the reports and the exact edits.

## Not covered

- Applying edits automatically: the author or the app applies them one by one with `apply_edit`.
- Compiling the paper or reading the PDF: the checker reads the LaTeX source.
- Proof that a citation candidate supports the claim: the candidates are leads to check.

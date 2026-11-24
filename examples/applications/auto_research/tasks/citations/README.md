# citation_check task

The `citation_check` task of auto_research (`tasks/citations/`). auto_research runs `citation_check()` on every bibliography that `bib_files` in rules.json names, with `investigate=off` and the reports under `drafts/citation_check/`; the function itself takes any reference list.

Pre-checks a reference list before submission: is every cited work real, and do its title, authors, year and venue match a record? Free sources only, no keys. It follows the same idea as CiteTrue and RefChecker. Exact database look-ups come first, and the model is used only for the references they cannot settle.

| Item | Contract |
| --- | --- |
| Purpose | Flag fabricated or misquoted references early. A pre-check, not proof: a work missing from free sources may still exist. |
| Input | `source`: a `.bib` file, a text file with a reference list, or the pasted list. `out_dir`: report folder; by default `citation_check/` next to the source file (the working directory for pasted text). `investigate`: `on` (default) lets the model search again for unsettled references, `off` skips it. Another value, an empty source, or a source with no readable reference raises `ValueError`. |
| Output | JSON with `status` (`PASS`, `PASS_WITH_NOTES`, `FAIL` when a reference was not found), the `report` path, `counts` per verdict and the `flagged` keys. Files in `<out_dir>/<timestamp>/`: `report.md` (Chinese), `report.json`. |
| Effects | Reads the source. Sends titles and author names to the public APIs of OpenAlex, Semantic Scholar, Crossref, arXiv, OpenReview and Open Library. Writes only the report folder. |
| Model calls | `reader`: one call, only for free-text input (a `.bib` file is parsed in code). `investigator` and `judge`: one call each, only when some reference is `unsure` or `inauthentic` and `investigate` is `on`. |
| Failure | A source that fails or rate-limits returns nothing, after three tries with back-off. A malformed role reply raises. The judge can pick only records it was shown; a verdict pointing elsewhere raises. |
| Tests | `tests/citations/test_refs.py`: parsing and matching. `tests/citations/test_workflow.py`: the entry with the sources replaced and scripted roles. |
| Usage | `openprogram programs run auto_research -a paper_dir=<repo> -a task=citation_check`, or `from auto_research.tasks.citations import citation_check` for any reference list |

## Process

1. **Read.** A `.bib` file is parsed in code. Other text goes to the `reader`, which splits it and copies out title, authors, year, venue, DOI and arXiv id without fixing anything.
2. **Look up** (`lookup.py`). A DOI is checked in Crossref; if it is missing or names another work, the reference gets `doi_incorrect`. An arXiv id is fetched from arXiv. Then the title is searched in OpenAlex and Semantic Scholar, and in Crossref, arXiv, OpenReview and Open Library when those two do not settle it.
3. **Score** (`refs.py`). Each record is scored on title similarity, author surname overlap and year gap. The verdict is `authentic` (title ≥ 0.93 and at least half the cited authors), `unsure` (title ≥ 0.80) or `inauthentic`. Notices:
   - `year`: a gap of two years or more.
   - `authors`: a cited author is missing from the record.
   - `venue_unconfirmed`: only preprints were found for a reference cited at a venue. Before this is reported, OpenReview and Crossref are searched as well.
4. **Investigate.**
   - The `investigator` writes up to three new queries for each unsettled reference: a short title, keywords plus a surname, a corrected spelling.
   - Every source runs them.
   - The `judge` sees the six closest records and picks one or `none`, with a short Chinese note.
5. **Report** (`report.py`). Flagged references come first, then a table of all of them.

## Not covered

- Paid sources: Google Scholar, Scopus, Web of Science and publisher databases.
- General web search: the free DuckDuckGo page and DBLP both return bot challenges to scripts.
- Whether a citing sentence describes the cited work correctly.
- Suggesting replacement references.

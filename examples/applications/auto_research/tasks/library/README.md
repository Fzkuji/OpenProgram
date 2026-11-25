# library tasks

The `library` tasks of auto_research (`tasks/library/`) work on the paper's bibliography as a whole: `citation_graph()` checks what the references are and what they point to, and `import_library()` brings in references kept in a reference manager. Both are plain code with no model calls.

`citation_check` is different: it checks that each reference is a real work cited correctly. `citation_graph` assumes that and asks whether a reference has since been retracted or corrected, and which works the references lean on that the paper does not cite.

## citation_graph

| Item | Contract |
| --- | --- |
| Purpose | Flag retracted and corrected references, and list the key works the references share that the paper does not cite, with a drawing of the graph. |
| Input | `paper_dir`: the project's repository. `min_shared`: how many of the paper's references must cite a work for it to count as missing (default `"3"`, at least 1). `out_dir`: the report folder (default `<paper_dir>/drafts/citation_graph`). The bibliographies are the `bib_files` of `workflow/rules.json` that exist, else every `*.bib` in the repository root; `drafts/` is never read. No folder, no entry or a bad `min_shared` raises `ValueError`. |
| Resolve | Each entry to an OpenAlex work: by DOI, else by arXiv id (its `10.48550` DOI), else a title search whose best hit matches the title at 0.93 or more. Entries not found are listed as unresolved. |
| Notices | OpenAlex `is_retracted`, and Crossref's `updated-by` (a notice about this work) and `update-to` (this DOI is itself a notice) of the entry's DOI. Types: retraction (also removal, withdrawal), correction (also corrigendum), erratum, expression of concern. Each becomes a problem with `key`, `title`, `kind`, `relation`, `notice_doi`, `notice_url`. |
| Missing works | The references of every resolved work, counted. A work cited by at least `min_shared` of them and absent from the bibliography (by OpenAlex id, DOI or normalized title) is a candidate. Ranked by that count, then by citations; the top 20 are kept with `openalex`, `title`, `year`, `venue`, `doi`, `url`, `citations`, `shared`, `cited_by` (the entry keys that cite it). |
| Output | JSON with `status`, `report`, `json`, `graph`, `html`, `run`, `entries`, `resolved`, `problems`, `missing`, `unresolved`, `unavailable_sources`. `FAIL`: a reference is retracted. `PASS_WITH_NOTES`: a correction, erratum or expression of concern, a missing-work candidate, an unresolved entry or a failed source. `PASS`: none of these. Files in `<out_dir>/<timestamp>/`: `report.md` (Chinese), `citation_graph.json`, `graph.json` (nodes and edges), `graph.html` (inline SVG, no script; your references in one colour, candidates in another, size by citations, a tooltip on each node), `run.json`, `run.md`. |
| Effects | Reads the bibliographies. Calls the public APIs of OpenAlex (one per entry, one per fifty candidates) and Crossref (one per DOI). Writes only the report folder. |
| Model calls | None. |
| Failure | A source that fails returns nothing and is named in `unavailable_sources`; the entries it could not check are listed as unresolved. Rerunning is safe. |
| Tests | `tests/library/test_graph.py`: the entry with `fetch` replaced. |
| Usage | `from auto_research.tasks.library import citation_graph` |

## import_library

| Item | Contract |
| --- | --- |
| Purpose | Merge an exported library (Zotero, Mendeley or any `.bib`, or Zotero's CSL-JSON) into the paper's bibliography. |
| Input | `paper_dir`: the project's repository. `bib_path`: the export; a `.json` file is read as CSL-JSON and converted to minimal BibTeX (type, title, author, year, venue, volume, number, pages, publisher, doi, url, arXiv eprint). `target`: the `.bib` to add to, relative to `paper_dir` or absolute; empty uses the first bibliography the paper uses, else `references.bib` (created). A missing folder or file, or an export with no entry, raises `ValueError`. |
| Merge | An entry is a duplicate when its DOI, arXiv id or normalized title is already in the target or earlier in the export; it is skipped. A new entry whose key is taken gets `_2`, `_3`, ... Added entries keep their original text (only a renamed key changes); existing entries are never changed or removed. |
| Output | JSON with `added` (keys, in order), `skipped` (`key`, `duplicate_of`, `by`: doi, arxiv or title), `renamed` (old key to new) and `target`. |
| Effects | Appends to the target file. No network. |
| Model calls | None. |
| Failure | Rerunning adds nothing new. |
| Tests | `tests/library/test_import.py`. |
| Usage | `from auto_research.tasks.library import import_library` |

## Not covered

- Retractions that neither OpenAlex nor Crossref records.
- References OpenAlex does not list: a work with no reference list adds nothing to the missing-work count.
- Deciding whether a missing work should be cited. The author does that.
- Syncing with a running Zotero: the author exports the library first.

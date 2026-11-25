# independence tools

One tool that asks whether the works supporting each claim are independent lines of evidence (`tasks/independence/`). claim_support counts "two or more supporting works", but two papers of one group, a paper and its own follow-up, or works built on one shared dataset are one line of evidence counted twice. Plain code, no model calls.

Learned from Hyperresearch's independence audit.

## source_independence

| Item | Contract |
| --- | --- |
| Signature | `source_independence(paper_dir, by_dataset="no", out_dir="")` |
| Purpose | Count, per claim, the independent groups among its supporting works and flag claims resting on one group. |
| Input | `paper_dir`: the project's repository. `workflow/claims.md` (`ValueError` without claims or with only the template). Supporting works: the rows of `workflow/claim_support.csv` with stance `supports` (template rows ignored); a claim with none takes the `\cite` keys of the paper's sentences that state it (main file flattened, floats left out; a sentence shares at least two and at least half of the claim's content words of four or more letters and numbers). The bibliographies as `citation_graph` finds them. `by_dataset`: `yes` also joins works whose abstracts name the same dataset of the paper (default `no`: shared datasets are listed only). `out_dir`: default `<paper_dir>/drafts/source_independence`. A missing folder raises `ValueError`. |
| Resolve | Each supporting key to an OpenAlex work with authorships, institutions, referenced works and the abstract: by DOI, else a title search (title similarity 0.93 or more; the closest, then the most cited), else the arXiv DOI. A key not in the .bib or not found is unresolved and not counted. |
| Datasets | The paper's own: names from a fixed list (ImageNet, CIFAR-10, GLUE, SQuAD, MMLU, …) that its text mentions, and capitalized names before dataset, benchmark, corpus or suite that carry a digit or an inner capital. Each work lists those its abstract names. |
| Grouping (plain code) | Union-find over the resolved works: two keys resolving to one OpenAlex work (`same work`), a shared OpenAlex author id (`shared author`, or `self-citation` when one also cites the other), and with `by_dataset` yes a shared dataset (`shared dataset`). Groups are transitive (A–B and B–C make one group) and named G1, G2, … in key order. Institutions are listed, never joined. |
| Output | Per claim `works`, `resolved`, `groups`, `flag` (`one group`: two or more resolved works in one group; `too few works`; `no works`; empty when two or more groups). `PASS`: every claim has two or more groups. `PASS_WITH_NOTES`: a claim has fewer, or a source failed. JSON: `status`, `report`, `csv`, `json`, `run`, `origin` (per claim `claim_support` or `paper`), `claims`, `single_group`, `unresolved` (key to reason), `unavailable_sources`. Files in `<out_dir>/<timestamp>/`: `independence.csv` (`claim, work, title, openalex, group, claim_groups, linked_to, reason, institutions, datasets, resolved`, one row per claim and work), `independence.json`, `report.md` (Chinese), `run.json`, `run.md`. |
| Effects | Reads claims.md, claim_support.csv, the LaTeX source and the bibliographies. One to three OpenAlex requests per supporting work. Writes only the report folder. |
| Model calls | None. |
| Failure | A failed OpenAlex request is named in `unavailable_sources` and its works are unresolved; never an exception. Rerunning is safe. |
| Tests | `tests/independence/test_independence.py`: with `fetch` replaced, two He et al. papers (one citing the other) counted as one group, an unresolved and a missing key, shared datasets listed and merged with `by_dataset`, the fallback to the paper's claim sentences, a failed source, transitive chains, dataset extraction, no claims. |
| Usage | `from auto_research.tasks.independence import source_independence` |

## Not covered

- Independence other than authorship and datasets: the same lab with disjoint authors, a shared funder, or a common upstream codebase.
- Author ids OpenAlex has split or merged wrongly; the report names the shared authors so the author can judge.
- Datasets the abstract does not name.
- Whether a supporting work really supports the claim; claim_support checks that.

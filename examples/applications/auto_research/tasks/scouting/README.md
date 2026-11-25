# scouting tasks

The `scouting` tasks of auto_research (`tasks/scouting/`) look outward from the paper: where to submit it (`venue_pick`), how its topics are trending (`topic_trends`), who cites it and what readers say once it is out (`after_release`), and which new papers this author will find relevant (`personal_rank`). All four are plain code with no model calls; every request goes through `citations.sources.fetch` (and `common.post` for Semantic Scholar's batch API), so a source that fails is named in `unavailable_sources` and the run goes on.

They follow tools authors already use: Elsevier JournalFinder, Springer Journal Suggester and the Web of Science manuscript matcher (`venue_pick`), 知网 AI 研学's topic trend analysis (`topic_trends`), Scite citation alerts and alphaXiv comments (`after_release`), and Scholar Inbox's personal recommender (`personal_rank`).

## venue_pick

| Item | Contract |
| --- | --- |
| Purpose | Recommend three to five venues for the paper, with the next deadline, page limit and double-blind policy. |
| Input | `paper_dir`: the project's repository. `out_dir`: the report folder (default `<paper_dir>/drafts/venue_pick`). The title and abstract come from the main `.tex` (`\title`, the `abstract` environment); the project's claim stands in when there is none. A missing folder raises `ValueError`. |
| Method | References: each `.bib` entry resolved to an OpenAlex work (`library.resolve`), its `primary_location.source` counted with weight `0.5 ** (age / 5)`. Similar works: the 100 OpenAlex works most relevant to title + abstract (`search=`, last five years), each source counted once per work. Preprint servers (source type `repository`, arXiv, SSRN, OpenReview, ...) are left out. Known venues (`VENUES`: ACL family, NeurIPS, ICLR, ICML, CVPR, ECCV, ICCV, AAAI, IJCAI, KDD, SIGIR, WSDM, CIKM, WWW, CHI, ICRA, IROS, TMLR, JMLR, TACL, AISTATS, UAI, COLM, COLING, WACV) merge across their OpenAlex names and map to a family of `tasks/venue/facts.py`; others keep the OpenAlex name. Score: the mean of the two shares. |
| Deadlines | `https://raw.githubusercontent.com/ccfddl/ccf-deadlines/main/conference/<area>/<name>.yml`, read with `yamlite` (or `yaml` when importable): the first `timeline[].deadline` on or after today, with its timezone and edition. |
| Output | JSON with `status`, `report`, `json`, `run`, `venues`, `unavailable_sources`. Files in `<out_dir>/<timestamp>/`: `report.md` (Chinese table: score, reference share, similar-work share, next deadline, page limit, double-blind, why), `venue_pick.json` (per candidate: `name`, `openalex_names`, `family`, `reference_share`, `similar_share`, `score`, `next_deadline`, `page_limit`, `double_blind`, `template`, `why`), `run.json`, `run.md`. `PASS` with three or more venues and every source answering, else `PASS_WITH_NOTES`. |
| Effects | Reads the bibliographies and the `.tex`. Calls OpenAlex (one per entry, one search) and GitHub raw (one per candidate venue). Writes only the report folder; `workflow/venue.md` is never changed. |

## topic_trends

| Item | Contract |
| --- | --- |
| Purpose | Count the works on each topic per year, normalized by all of OpenAlex, and label the trend 升温 / 拥挤 / 冷门 / 平稳. |
| Input | `paper_dir`. `queries`: separated by `;` (empty: the `Queries:` of `workflow/watch.md`, else the idea titles of the newest `drafts/idea_generation/*/ideas.json` or `workflow/ideas.json`, else the field survey directions, else the `##` headings of `workflow/literature.md`, else the project's claim; six at most). `since`: first year (default `2016`, at most three years ago). `out_dir` (default `<paper_dir>/drafts/topic_trends`). No query or a bad `since` raises `ValueError`. |
| Method | Per query: OpenAlex `group_by=publication_year` with `title_and_abstract.search` (`survey.search.yearly_counts`), divided by the same group-by over all works; growth `g = (share_last / share_first) ** 0.5 - 1` over the last three complete years; the top 8 co-occurring `topics.id` in those years. |
| Labels | In order, with `n` the works of the last complete year: `n < 50` 冷门; `g >= 25%` 升温; `n >= 1000` 拥挤; otherwise 平稳 (constants `COLD`, `RISING`, `CROWDED`). |
| Output | JSON with `status`, `report`, `json`, `run`, `labels`, `unavailable_sources`. `trends.md` (summary table, an inline SVG line chart per query of works per 10,000 with the partial current year dashed, co-occurring topics, the rules), `trends.json`, `run.json`, `run.md`. |
| Effects | Three OpenAlex calls per query plus one. Writes only the report folder. |

## after_release

| Item | Contract |
| --- | --- |
| Purpose | After publication: list citing works, classify each citing sentence and reader comment as supporting / contrasting / mentioning, and put what needs a reply or an erratum check first. |
| Input | `paper_dir`. `work`: OpenAlex id, DOI or arXiv id (empty: `published` in `workflow/rules.json`, else a `Published:` line in `workflow/project.md`). OpenReview forum: `openreview` in rules.json or an `OpenReview:` line in project.md. Neither raises `ValueError`. `out_dir` (default `<paper_dir>/drafts/after_release`). |
| Sources | OpenAlex `works?filter=cites:<id>` (200 newest). Semantic Scholar `/graph/v1/paper/{ARXIV:id or DOI:doi}/citations?fields=contexts,intents,...` (one item per sentence; a citing work with sentences is not repeated as a bare citation). alphaXiv: `https://api.alphaxiv.org/papers/v3/<arXiv id>` gives the `groupId`, then `papers/v3/legacy/<groupId>/comments` (public, top-level comments not by the authors; skipped with a note when unreachable). OpenReview `api2.openreview.net/notes?forum=<id>`: comment notes not signed by the authors. |
| Stance rules | Contrasting: however, in contrast, contrary to, fail(s/ed) to, could not reproduce, inconsistent / not consistent with, contradict, disagree, challenges the claim, over/underestimate, does not hold, limitation of, flawed. Supporting: consistent with, in line with, agree with, confirm, corroborate, supports the findings, reproduce the results, as shown by; or intent `result` with similar / comparable / as reported. Else mentioning. Needs: `erratum` for a comment with an error cue (an error in, a bug, mistake, typo, incorrect, erratum, wrong, data leakage, could not reproduce) or a contrasting sentence with one; `reply` for other contrasting sentences and comments without an author answer. |
| State | `workflow/after_release.csv`: `id, kind, stance, quote, handled, title, url, date, needs` (author columns kept). A run adds only ids it has not seen; `handled` is the author's note and is never overwritten. |
| Output | JSON with `status`, `report`, `json`, `run`, `state`, `new`, `needs_action`, `counts`, `unavailable_sources`. `report.md` (new items, erratum and reply first; then what is still unhandled; 100 rows per table), `after_release.json`, `run.json`, `run.md`. `PASS_WITH_NOTES` while something needs action or a source failed. |

## personal_rank

| Item | Contract |
| --- | --- |
| Purpose | Rank new papers by what this author judged relevant before. |
| Labels | From `workflow/watch.csv` (the watch task's `verdict` is the model's and is not used): a `relevance` / `label` / `relevant` / `rating` column the author added (1, yes, relevant, 相关 / 0, no, irrelevant, 无关), else the `handled` note (irrelevant, unrelated, skip, 无关, 忽略, ... is not relevant; any other note is relevant), else unlabeled. At least 5 of each; otherwise the result gives `need_more` and nothing else changes. |
| Candidates | Unlabeled watch.csv rows, and the arXiv API listing of the project's categories (`cat:` query, newest 300, last two days): `Categories:` in watch.md, else `arxiv_categories` in rules.json, else the primary categories of the relevant arXiv rows. |
| Model | SPECTER2 vectors (`POST /graph/v1/paper/batch?fields=title,abstract,embedding.specter_v2`, 500 ids per call), centered on the mean of the run's vectors and unit-normalized; logistic regression in plain Python (full-batch gradient descent, L2, classes weighted equally). Candidates without an embedding, or every candidate when fewer than 5+5 labeled rows have one, are scored by the same regression on TF-IDF of title (twice) and abstract. |
| Output | JSON with `status`, `report`, `json`, `run`, `labels`, `ranked`, `methods`, `top`. `ranked.md` (score, source, paper, date, method), `ranked.json`, `run.json`, `run.md`. A `score` column is added to watch.csv: the score for unlabeled rows, empty for labeled ones; every other column is kept. |
| Effects | Rewrites `workflow/watch.csv` with the score column; `related_work_watch` keeps it and other added columns. Row ids: `arXiv:`/`doi:` as the watch task writes them, a bare arXiv id, DOI or S2 id, else the arXiv or doi.org url. A rate-limited POST is tried twice more. |

## Common

| Item | Contract |
| --- | --- |
| Model calls | None. |
| Failure | A source that fails returns nothing and is named in `unavailable_sources`. Rerunning is safe; `after_release` adds only unseen items. |
| Tests | `tests/scouting/test_scouting.py`: every request replaced; YAML deadline parsing, venue merging, trend labels, stance rules, the state file, logistic regression on toy vectors, label rules and the watch.csv score column. |
| Usage | `from auto_research.tasks.scouting import venue_pick, topic_trends, after_release, personal_rank` |

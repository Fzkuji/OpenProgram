# field_survey task

The `field_survey` task of auto_research (`tasks/survey/`). auto_research calls `research_survey()` with `direction`, `reader_level`, `purpose`, `our_work` and `preset` and saves the result under `workflow/surveys/<direction>/` of the project.

Surveys a research field from a direction and saves it as one offline HTML page. The page has three parts for a researcher entering the field:
- what the field does and what has been done: an overview, a map of the research lines, and one chapter per line;
- what it has worked on recently: yearly counts and the new settings, benchmarks and methods;
- which directions can be followed: open problems that rest on limitations stated in at least two papers.

The same run serves a full survey, scouting a new direction, and the related-work section of our own paper.

| Item | Contract |
| --- | --- |
| Purpose | Turn a direction into a survey whose every claim points to a paper and a location in it. |
| Input | `direction`: the field, in a few words. `reader_level`: what the reader already knows; empty lets the planner state it. `purpose`: `survey`, `scouting` or `related_work`. `our_work`: what our paper contributes, needed by `related_work`. `since`: the first year searched (default eight years back). `recent_months`: the window of the recent-trends part (18). `min_papers`: the fewest core papers for a PASS (70). `max_read`: the most papers whose body is read (120). `language`: `zh` or `en`. `out_dir`: the base folder, by default `./surveys/<direction>`. `preset`: a name from `presets.py` whose fixed research lines replace the planner's (see Presets); empty lets the planner write them. Bad values, an unknown preset among them, raise `ValueError`. |
| Output | JSON with `status` (`PASS`; `PASS_WITH_NOTES` when audit issues remain or a source failed; `FAIL` when fewer than `min_papers` core papers were found), `page`, `report`, `run`, `survey`, `candidates`, `core`, `chapters`, `notes`, and for `related_work` also `related_work` (.tex) and `bib`. Files in `<out_dir>/<timestamp>/`: `survey.html`, `survey.json` (everything the page is built from), `candidates.json` (every candidate with its screening decision and reason), `report.md` (Chinese), `run.json` and `run.md`. |
| Effects | Sends queries and paper ids to arXiv, Semantic Scholar and OpenAlex, and reads arXiv HTML or ar5iv pages. Writes only the output folder. |
| Model calls | About 50 to 90 for a field of 100 papers: one scope call, one screen per 40 candidates, one gap call, one read per 3 papers, one chapter per line, one audit per chapter (plus one rewrite and one audit for each chapter that fails), the overview, the trends, the directions, and the related work when asked. |
| Failure and cancellation | A source that fails or rate-limits is skipped after retries and named in the report. A malformed or out-of-range role reply raises after one retry. `check_cancelled()` runs before every network batch and model call. |
| Tests | `tests/survey/test_parts.py`: merging, section parsing, every reply check, rendering, LaTeX and the presets. `tests/survey/test_workflow.py`: the whole entry with the network replaced and every role scripted. |
| Usage | `openprogram programs run auto_research -a paper_dir=<repo> -a task=field_survey -a direction="vision-language model reasoning" -a reader_level="Transformers, CLIP-style pretraining, RL fine-tuning of LLMs"`, or `from auto_research.tasks.survey import research_survey` for the other options |

## Stages

| Stage | Who | What |
| --- | --- | --- |
| scope | planner | Title, reader prerequisites, boundary, research questions, three to eight research lines with definitions and queries, survey queries. |
| retrieve | code | Every query in arXiv, Semantic Scholar and OpenAlex (`search.py`, over the shared source client of `tasks/citations/sources.py`), merged by arXiv id, DOI and title. Surveys are searched separately. |
| screen | screener | Each candidate by id: `core`, `adjacent`, `survey` or `exclude`, its line, its kind and a reason. |
| snowball | code | For the eight most cited core papers, the papers citing them and the papers they cite (OpenAlex), then screened. |
| baseline | code, planner | The section headings of up to three existing surveys with an HTML version are mapped onto our lines. Gaps get new queries or a new line, and the results are screened. |
| read | code, reader | Body sections from arXiv HTML or ar5iv (`fulltext.py`), introduction, limitations, method and results first, about 12,000 characters. The reader records problem, mechanism, the corpus papers it builds on and how it differs, findings and limitations. Each point carries its locator, and the check refuses a locator from another paper. Papers without HTML are read from the abstract and marked so. |
| write | writer | One chapter per line with two or more papers, following `rules/writing.md`. Then the overview and the map edges, from lineage counts that code takes from the reading notes. |
| audit | auditor | Each chapter against the notes it cites: judgements without mechanism or evidence, unsupported claims, wrong citations, over-explained prerequisites, template sections. A failing chapter is rewritten once and audited again. What remains goes into the report. |
| trends | code, analyst | Yearly OpenAlex counts per line and half-yearly corpus counts are computed in code. The analyst writes the summary, the rising lines, the new items and the saturated problems, each with recent papers. |
| directions | analyst | Up to six cards. Each rests on limitations of at least two papers and names the active work, the entry cost and a first experiment. Code counts how many of the recent papers are active work. |
| related work | writer | Only for `related_work`: one paragraph per relevant line ending with how `our_work` differs. Code turns the citations into `\cite` keys and writes the `.bib`. |
| render | code | `render.py` and `templates/page.html`: one offline page with inline citations and hover cards, the field map, small multiples of the yearly counts, the direction cards, a filterable paper list and the search scope. |

## Presets

A preset fixes the research lines of a field that is surveyed again and again. Its tracks replace the lines of the scope stage: the planner writes only the title, prerequisites, boundary, questions and survey queries around them, and the baseline stage maps gaps onto the tracks but opens no new line. Each track lists its queries in priority order; retrieval runs them in that order, and the first is the one the yearly counts use. The preset's year is the default of `since`. Presets live in `presets.py`.

| Preset | Tracks | Since |
| --- | --- | --- |
| `llm_knowledge` | Memory architecture (persistent and long-horizon memory), Memory lifecycle (retrieval, consolidation, forgetting, evaluation and security), Modular experts (routing, composition and pluggable components), Knowledge adaptation (knowledge editing and continual learning) | 2025 |

Usage: `openprogram programs run auto_research -a paper_dir=<repo> -a task=field_survey -a direction="LLM knowledge: memory, modular experts and knowledge adaptation" -a preset=llm_knowledge`.

## Roles

- `planner` plans the scope and the gap searches and never states paper facts. It keeps a thread, so the gap call sees the scope it wrote.
- `screener` judges only the records given.
- `reader` records what the excerpts state.
- `writer` writes only what the notes support.
- `analyst` grounds every trend and direction in papers.
- `auditor` checks and never rewrites.

Separating the reader from the writer keeps the facts tied to locators before any prose exists. Separating the auditor from the writer keeps the check independent of the text it judges.

## Limits

- Free sources only. Without `S2_API_KEY` Semantic Scholar often answers 429. Without `OPENALEX_API_KEY` OpenAlex shares a small daily budget per IP address, which limits the snowball and the yearly counts. Set either key to raise the limit.
- Full text comes only from arXiv HTML and ar5iv. Papers outside arXiv are read from their abstract.
- The audit is a model judgement on the notes, not on the papers themselves. A note can be wrong.
- Yearly counts match the line's first query in titles and abstracts. They show the direction of change, not the exact size of a line.

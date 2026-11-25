# discovery tasks

The discovery tasks of auto_research (`tasks/discovery/`): `data_explore` looks for surprising findings in data files without a question, `ablation_target` finds the code component that matters most for the metric, and `notes_ingest` brings the author's own notes into the world model. Learned from Ai2 AutoDS (Bayesian surprise as the reward of a tree search over hypotheses), MLE-STAR (ablation to find the block worth refining) and Microsoft Discovery's Bookshelf (a team's documents turned into a knowledge graph).

## data_explore

| Item | Contract |
| --- | --- |
| Purpose | Open-ended discovery: hypotheses about the data the author did not think to ask, ranked by how much an analysis changed the belief in them. |
| Input | `paper_dir`: the project's Git repository. `data`: CSV, TSV, JSON or JSONL files inside it, comma-separated. `budget`: hypotheses to try, 1 to 40 (default 12). `out_dir`: the report folder (default `<paper_dir>/drafts/data_explore`). A missing repository, an empty `data`, a file outside the repository, missing, inside `.git` or of another type, or another `budget` raises `ValueError`. |
| Search | Plain code. The root is a summary of the data: per file its rows, per column its type, missing count, range and mean (numbers) or distinct count and most common values (text), and five sample rows. Each step descends from the root: a node with fewer children than 1 + sqrt(visits) gets the new child, otherwise the child of highest UCB1 (mean surprise + sqrt(ln parent visits / visits)) is entered; nodes at depth 4 are not expanded. The new node's surprise is added to it and every node above it. |
| Model calls | Per hypothesis: `proposer` (one hypothesis, a child of the selected node: a follow-up of the path shown, or a fresh one at the root), `believer` (probability it holds, before any result), `analyst` (a Python script; stdlib plus pandas, numpy or scipy when installed; a second call with the error when the first fails), `believer` again (probability given the script and its output). |
| Analysis run | The script runs with `python -I` in a temporary folder that holds copies of the data files at their repository paths, a 120-second limit, a minimal environment and an audit hook: reads only in that folder and where Python and its libraries live, writes only in that folder, no sockets or new processes. Its last JSON line `{"result", "statistic", "p"}` is the outcome; `p` must be 0 to 1 or null. A script that fails twice leaves the belief where it was (surprise 0). |
| Surprise | KL(Bernoulli(posterior) ‖ Bernoulli(prior)) in nats, beliefs clipped to [0.001, 0.999]; the shift (posterior − prior) gives its direction. |
| Output | JSON with `status`, `report`, `json`, `hypotheses`, `run`, `candidates` and `ranked` (id, parent, hypothesis, prior, posterior, surprise, status). `PASS`: every analysis ran; `PASS_WITH_NOTES`: some failed; `FAIL`: none ran. `<out_dir>/<timestamp>/` holds `scripts/H<n>.py`, `outputs/H<n>.txt`, `report.md` (Chinese: hypotheses ranked by surprise, and the ones of at least 0.1 nats, up to five, drafted as `findings.md` entries for the author to copy), `explore.json`, `run.json`, `run.md`. |
| Effects | Appends to `workflow/hypotheses.csv` (`id,parent,hypothesis,prior,posterior,surprise,statistic,p,script,output`; `parent` empty under the root; `script` the saved script's path; `output` the JSON result, or `error: ...`). Ids continue across runs. `findings.md` is never written. |

## ablation_target

| Item | Contract |
| --- | --- |
| Purpose | Find which part of the experiment code the metric depends on, so the experiment loop can narrow `Edit` to it. |
| Input | `paper_dir`: the project's Git repository with `workflow/experiment.md` (`Edit`, `Command`, `Metric` filled in; `Direction`; `Budget`). `components`: comma list of `name=file:start-end`, `name=file:function`, `file:function` or `function` (also `Class.method`), in the `Edit` files; empty lets the splitter propose them. `out_dir`: the report folder (default `<paper_dir>/drafts/ablation_target`). A component outside the `Edit` files or their lines, an unknown or ambiguous function, or repeated names raise `ValueError`. |
| Reference | The best kept run of `results.tsv` by `Direction` whose commit is in the repository; when none is kept, HEAD, run once in a detached worktree. Components are read from that commit. |
| Model calls | `splitter` (only when `components` is empty): 3 to 8 components with file and line range; ranges outside the files and repeated names are dropped in code. `ablator`: per component, an exact `quote` inside its lines and a `replacement` (identity, mean, default, constant or skip); the quote must occur once, lie in the component's lines and the edited Python must parse; otherwise one more call with the problem. |
| Runs | Per component: a worktree from the reference on a new branch `ablate/<name>` (`-2`, `-3`, ... when taken), the edit committed as `ablation: <name>`, `Command` run there within `Budget` (`5 min`, `300 s`, `1 h`; 600 s when unreadable), `<Metric>: <number>` read from the output. Worktrees are removed at the end; branches stay. |
| Output | JSON with `status`, `report`, `json`, `run`, `reference`, `suggestion` and `components` (name, file, start, end, branch, metric, contribution, impact, status), ranked by impact. contribution = ablated metric − reference; impact = contribution for lower-is-better, its negative for higher-is-better (positive: the component helps). `PASS`: every component measured; `PASS_WITH_NOTES`: some not; `FAIL`: none, or no reference. `<out_dir>/<timestamp>/`: `report.md` (Chinese: the ranking and the suggested narrower `Edit`), `ablation.json`, `logs/`, `run.json`, `run.md`. |
| Effects | Appends one row per run to `workflow/results.tsv`: the ablation commit, metric, status `discard` (`crash` without a metric), seconds, description `ablation: <name>`, branch `ablate/<name>`, parent the reference commit when it is a kept run. Adds the optional `branch` and `parent` columns to the header when missing. The working tree is never touched. |

## notes_ingest

| Item | Contract |
| --- | --- |
| Purpose | Put what the team wrote outside the paper (meeting notes, slides, tables) into `workflow/world.json`, quoted and located, for the author to accept in the research map. |
| Input | `paper_dir`: the project's Git repository. `folder`: a folder inside it (default `workflow/notes`). `out_dir`: the report folder (default `<paper_dir>/drafts/notes_ingest`). A folder outside the repository or missing, or one with no readable text, raises `ValueError`. |
| Reading | Plain code, no extra packages. `.md`/`.markdown`/`.txt` by line (`L3-7`), `.pdf` through `pdftotext` by page (`p2`), `.pptx` by slide in presentation order, speaker notes included (`slide4`), `.xlsx` by sheet row (`Results!r2-5`), `.docx` by paragraph (`para3`), `.csv` by row (`row2`). Other files, and PDFs without `pdftotext`, are listed as skipped. Units of one file are chunked up to 3,000 characters; chunks go to the extractor in batches up to 12,000; at most 200 chunks (a cut is stated). |
| Model calls | `extractor`: one call per batch with the chunks and the entities already known. It returns entities (research_map's types), relations (research_map's types, ends by name or id) and conclusions, each with its chunk and a verbatim quote. |
| Checks | Plain code. The quote must stand in its chunk (whitespace and case folded); the locator is narrowed to the units it covers. A relation whose ends are not entities is dropped. An entity with the same name and type, or a relation with the same ends and type, is already there and is not added again; existing entries, approved or not, are never changed. |
| Output | JSON with `status`, `report`, `json`, `world`, `run`, `added` (entity ids, relations, conclusion ids), `repeated`, `dropped`, `skipped`. `PASS`: items added or already present, nothing dropped or skipped; `PASS_WITH_NOTES`: also dropped items, skipped files or a cut; `FAIL`: nothing could be backed by a quote. `<out_dir>/<timestamp>/`: `report.md` (Chinese: what was added with source and quote, what was dropped, skipped files), `notes.json`, `run.json`, `run.md`. |
| Effects | Appends to `workflow/world.json` in research_map's schema: entities `{id, name, type, note, source, quote, approved: false}` (a conclusion is a `result` entity whose note is the statement), relations `{from, to, type, evidence, note, source, quote, approved: false}`; `source` is `notes:<file>#<locator>`; `evidence` is `file:line` for text notes, else the source. Sets `notes_at`. Nothing else in `workflow/` is written. |

## Shared

| Item | Contract |
| --- | --- |
| Failure | A role reply missing its required fields raises. Bad citations, quotes, edits and scripts never raise; they are retried once where stated, then dropped or recorded with the reason. Rerunning is safe: hypothesis ids continue, branch names get a suffix, notes already in the world model are not added twice. |
| Tests | `tests/discovery/test_explore.py` (scripted roles on a tiny CSV, real guarded runs of generated scripts, a script blocked from reading outside its folder, the tree's parents, surprise values), `tests/discovery/test_ablation.py` (a tiny repository whose metric really runs in each worktree, named and proposed components, an edit outside the component retried, a reference run from HEAD, results.tsv columns and the experiment check), `tests/discovery/test_notes.py` (PPTX, XLSX and DOCX made in the test with zipfile, a hand-written PDF when `pdftotext` is present, locators, approved entries untouched, a second run adding nothing). |
| Usage | `from auto_research.tasks.discovery import data_explore, ablation_target, notes_ingest` |

## Not covered

- Data files other than CSV, TSV, JSON and JSONL in `data_explore`; images and scanned PDFs in `notes_ingest`.
- Building an environment for the ablation runs: they use the environment `Command` runs in, as the experiment loop does.

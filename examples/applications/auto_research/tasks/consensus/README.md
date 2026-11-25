# consensus_analysis task

The `consensus_analysis` task of auto_research (`tasks/consensus/`). It answers one question about the project's data by running several independent analyses and reporting only what they agree on. The idea comes from FutureHouse Finch, which runs several analysis trajectories and reports their consensus, and Kosmos, which links each statement to its data.

| Item | Contract |
| --- | --- |
| Purpose | A checked answer to a data question ("does warmup raise accuracy?"), with the numbers it rests on linked to their rows, and a plain statement when the analyses disagree. |
| Input | `paper_dir`: the project's Git repository. `question`: the question, in any language. `runs`: how many independent analyses, 2 to 7 (default 3). `data`: extra CSV, TSV or JSON files inside the repository, comma-separated. `out_dir`: the report folder (default `<paper_dir>/drafts/consensus_analysis`). A missing repository, an empty question, another `runs`, a data file outside the repository, missing or of another type, or no data at all raises `ValueError`. |
| Data | `workflow/results.tsv`, `evidence.csv`, `baselines.csv`, `findings.md`, `experiment_plan.csv` (missing files and template example rows are skipped), then the `data` files. A CSV/TSV row is its data row number after the header; a Markdown row is its line number; a JSON list is one row per item, other JSON is its pretty-printed lines. Each file shows at most 200 rows and 60,000 characters; a cut says so, and rows past it cannot be cited. |
| Output | JSON with `status`, `report`, `json`, `run`, `verdict`, `agreement` and `answers` (run, verdict, answer, dropped citations). `PASS`: at least two thirds of the runs give the majority verdict and it is not `cannot_tell`. `PASS_WITH_NOTES`: at least half do. `FAIL`: fewer; the runs disagree and the data does not settle the question. Files in `<out_dir>/<timestamp>/`: `report.md` (Chinese: the consensus answer, agreement, agreed evidence with citations, disagreements and caveats, unconfirmed claims, each run), `consensus.json`, `run.json`, `run.md`. |
| Effects | Reads the files above. Writes only the report folder; nothing in `workflow/` is changed. |
| Model calls | `analyst`: one call per run on the same question and data, each given a different angle in its context (raw rows first, summary statistics first, confounds first, ...). Each returns a one-sentence answer, a verdict (`yes`, `no`, `mixed`, `cannot_tell`), key numbers each citing `file:row`, and caveats. |
| Consensus | Plain code. Every citation is checked: the row must exist as shown and hold the number (as written, or rounded to the decimals cited); others are dropped and counted. The majority verdict wins, a tie going to the more cautious one (`cannot_tell`, `mixed`, `no`, `yes`). Agreement is the share of runs with the majority verdict. A `file:row` cited by at least half the runs (and at least two) is agreed evidence; one cited by a single run is unconfirmed. The answer shown is the majority run citing the most agreed evidence. |
| Failure | A reply without an answer or with an unknown verdict raises. Bad citations never raise; they are dropped and counted per run. Rerunning is safe. |
| Tests | `tests/consensus/test_workflow.py`: the entry with the analyst scripted for agreeing, disagreeing and invalid-citation replies, the `runs` bounds, the data paths and the file cap. |
| Usage | `from auto_research.tasks.consensus import consensus_analysis` |

## Not covered

- Computing new statistics: the analysts read the rows; a number not written in a row cannot be cited.
- Whether the data was produced correctly: that is the experiment and baselines checks' job.
- Files other than CSV, TSV and JSON in `data`.

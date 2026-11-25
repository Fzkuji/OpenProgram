# idea_generation task

The `idea_generation` task of auto_research (`tasks/ideas/`). auto_research calls `idea_generation()` with `direction` once the `gaps` gate finds something to work from, and keeps the reports in `drafts/idea_generation/`.

Proposes research ideas from the gaps the project has already surveyed, checks each against the literature, and ranks them. The `idea` task is different: it only checks that the claims in `workflow/claims.md` are written and approved. An idea the author picks here becomes a claim there, written by the author or their agent. This task never writes `claims.md`.

| Item | Contract |
| --- | --- |
| Purpose | Turn surveyed gaps into a short, ranked list of ideas, each with the closest prior work found. |
| Input | `paper_dir`: the project's Git repository. `direction`: the surveyed field to work from; empty uses the newest survey in every folder of `workflow/surveys/`. `count`: how many ideas, 1 to 8 (default 5). `out_dir`: the report folder (default `<paper_dir>/drafts/idea_generation`). `rounds`: evolution rounds, 0 to 5 (default 0: no evolution, the run is as described without them). `population`: the most ideas kept alive between rounds, `count` to 40 (default 0, meaning `count`). A missing repository, another count, rounds or population, or no gap at all raises `ValueError`. |
| Gaps | `G1`, `G2`, ...: the open directions (`directions.cards`) of the newest `survey.json` that field_survey wrote, with their problem, cost and first step. `L1`, `L2`, ...: the surveyed works listed in `workflow/literature.md`; the template line is skipped. |
| Output | JSON with `status`, `report`, `ideas`, `run`, the count per novelty and `unavailable_sources`. `FAIL`: every idea is already known. `PASS_WITH_NOTES`: no idea is clearly novel, or a source failed. `PASS`: at least one novel idea. Files in `<out_dir>/<timestamp>/`: `ideas.md` (Chinese), `ideas.json`, `run.json`, `run.md`. With `rounds` > 0, `ideas.json` adds `rounds` (per round: new ideas, matches, judge calls, retired, alive, the meta-review), `lineage` (each listed idea's round, method, parents and gaps), `retired` (id, title, final Elo, round) and `clusters` (kept idea and its near-duplicates); each listed idea carries `near_duplicates`, and `ideas.md` adds the lineage, the rounds, the meta-reviews, the near-duplicates and the retired ideas. |
| Effects | Reads `workflow/surveys/` and `workflow/literature.md`. Sends each idea's queries to the public APIs of arXiv, Semantic Scholar and OpenAlex (`../survey/search.py`), for the last six years. Writes only the report folder. |
| Model calls | `ideator`: one call that proposes the ideas, each naming the gap ids it addresses and one to three search queries. `assessor`: one call that judges every idea against its eight closest records (`novel`, `incremental`, `known`, `unverified`), names the closest records by id, and rates feasibility and impact from 1 to 5. `judge`: one call that referees every pair of ideas once (A/B order alternating) and says which is more worth pursuing and why; with `rounds` > 0, calls of at most 20 matches. `evolver` (rounds only): one call per round that makes up to four new ideas from the top four by rating, with their judge reasons, closest work and the last meta-review's advice; each idea says how it was made (`combine` two parents, `refine` or `simplify` one, `out_of_box` none) and names its gaps. `meta_reviewer` (rounds only): one call per round over every assessment and judge reason, returning the strengths and weaknesses that recur across the population and advice for the next round. |
| Failure | A malformed reply, an idea naming an unknown gap, an assessment naming a record it was not shown, or `incremental`/`known` without a record raises, and so does a judge reply that leaves a match undecided, an evolved idea whose parents are not top ideas or do not fit its method, or a meta-review without advice. A source that fails returns nothing and is named in the report. Rerunning is safe. |
| Tests | `tests/ideas/test_workflow.py`: the entry with the search replaced and scripted roles, without rounds and with one and two rounds (judge batches, Swiss pairing, population cap, lineage, meta-review advice fed on), Swiss pairing and clustering on their own. `tests/test_workflow.py`: the gate and the hand-off from auto_research. |
| Usage | `openprogram programs run auto_research -a paper_dir=<repo> -a task=idea_generation -a direction="LLM agent memory"`, or `from auto_research.tasks.ideas import idea_generation` |

## Process

1. **Gather** (code). The gaps, each with an id.
2. **Generate.** The `ideator` proposes ideas from incremental to ambitious: hypothesis, approach, expected outcome, effort, risk, gaps and queries.
3. **Search** (code). Every query of an idea runs in every source. The records that share the most words with the idea are kept.
4. **Assess.** The `assessor` sees each idea with its records only.
5. **Tournament.** The `judge` referees every pair of ideas once (at most 28 matches for 8 ideas), as AI co-scientist ranks its hypotheses.
6. **Rank** (code). Elo ratings from the matches (start 1200, K 32, averaged over three orders of the matches). Ideas a record already does (`known`) go last; the rest by rating, then novelty, feasibility and impact. An idea whose search found no record is `unverified`, whatever the assessor said. The report lists each idea's rating, wins and losses, and the judge's reason for each match.

## Not covered

- Proof of novelty: the search is bounded, and an unverified idea may still have prior work.
- Full text: the assessor reads titles and abstracts.
- Choosing an idea or writing it as a claim. The author does that, then the `idea` task checks it.

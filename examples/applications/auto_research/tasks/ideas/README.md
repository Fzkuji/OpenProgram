# idea_generation task

The `idea_generation` task of auto_research (`tasks/ideas/`). auto_research calls `idea_generation()` with `direction` once the `gaps` gate finds something to work from, and keeps the reports in `drafts/idea_generation/`.

Proposes research ideas from the gaps the project has already surveyed, checks each against the literature, and ranks them. The `idea` task is different: it only checks that the claims in `workflow/claims.md` are written and approved. An idea the author picks here becomes a claim there, written by the author or their agent. This task never writes `claims.md`.

| Item | Contract |
| --- | --- |
| Purpose | Turn surveyed gaps into a short, ranked list of ideas, each with the closest prior work found. |
| Input | `paper_dir`: the project's Git repository. `direction`: the surveyed field to work from; empty uses the newest survey in every folder of `workflow/surveys/`. `count`: how many ideas, 1 to 8 (default 5). `out_dir`: the report folder (default `<paper_dir>/drafts/idea_generation`). A missing repository, another count, or no gap at all raises `ValueError`. |
| Gaps | `G1`, `G2`, ...: the open directions (`directions.cards`) of the newest `survey.json` that field_survey wrote, with their problem, cost and first step. `L1`, `L2`, ...: the surveyed works listed in `workflow/literature.md`; the template line is skipped. |
| Output | JSON with `status`, `report`, `ideas`, `run`, the count per novelty and `unavailable_sources`. `FAIL`: every idea is already known. `PASS_WITH_NOTES`: no idea is clearly novel, or a source failed. `PASS`: at least one novel idea. Files in `<out_dir>/<timestamp>/`: `ideas.md` (Chinese), `ideas.json`, `run.json`, `run.md`. |
| Effects | Reads `workflow/surveys/` and `workflow/literature.md`. Sends each idea's queries to the public APIs of arXiv, Semantic Scholar and OpenAlex (`../survey/search.py`), for the last six years. Writes only the report folder. |
| Model calls | `ideator`: one call that proposes the ideas, each naming the gap ids it addresses and one to three search queries. `assessor`: one call that judges every idea against its eight closest records (`novel`, `incremental`, `known`, `unverified`), names the closest records by id, and rates feasibility and impact from 1 to 5. |
| Failure | A malformed reply, an idea naming an unknown gap, an assessment naming a record it was not shown, or `incremental`/`known` without a record raises. A source that fails returns nothing and is named in the report. Rerunning is safe. |
| Tests | `tests/ideas/test_workflow.py`: the entry with the search replaced and scripted roles. `tests/test_workflow.py`: the gate and the hand-off from auto_research. |
| Usage | `openprogram programs run auto_research -a paper_dir=<repo> -a task=idea_generation -a direction="LLM agent memory"`, or `from auto_research.tasks.ideas import idea_generation` |

## Process

1. **Gather** (code). The gaps, each with an id.
2. **Generate.** The `ideator` proposes ideas from incremental to ambitious: hypothesis, approach, expected outcome, effort, risk, gaps and queries.
3. **Search** (code). Every query of an idea runs in every source. The records that share the most words with the idea are kept.
4. **Assess.** The `assessor` sees each idea with its records only.
5. **Rank** (code). By novelty (novel, unverified, incremental, known), then feasibility, then impact. An idea whose search found no record is `unverified`, whatever the assessor said.

## Not covered

- Proof of novelty: the search is bounded, and an unverified idea may still have prior work.
- Full text: the assessor reads titles and abstracts.
- Choosing an idea or writing it as a claim. The author does that, then the `idea` task checks it.

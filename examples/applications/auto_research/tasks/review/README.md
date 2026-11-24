# paper_review task

The `paper_review` task of auto_research (`tasks/review/`). auto_research calls `paper_review()` on the working tree, or on `draft_dir`, with `reviewer_model`, and keeps the reviews in `drafts/paper_review/`.

Reviews the whole paper as a program committee member of its venue would: contribution, rigor and consistency, each weakness with a severity, a section and a fix, and a score. It never edits the paper. Rounds happen across runs: the author (or the app's agent) fixes the weaknesses in a draft copy and runs the task on that draft, and the new review says for each earlier weakness whether it is resolved. paper_check checks rules paragraph by paragraph; this task judges the paper as a submission.

| Item | Contract |
| --- | --- |
| Purpose | A venue-level review of the current paper before submission, with the earlier round's weaknesses tracked. |
| Input | `paper_dir`: the paper's Git repository; `workflow/rules.json` names `main_tex`. `draft_dir`: a revised copy to review instead; empty reviews the working tree. `venue`: the target venue; empty reads the `Venue` and `Requirements` parts of `workflow/venue.md`. `reviewer_model`: the model the reviewer runs on (`provider:model`), so the paper is not reviewed by the model that wrote it; empty uses the caller's model. `out_dir`: the review folder (default `<paper_dir>/drafts/paper_review`). A missing repository or main file, or a main file with no text, raises `ValueError`. |
| Output | JSON with `status`, `report`, `review`, `run`, `score`, `round` and the weakness counts by severity. `FAIL`: score below 7 or a fatal weakness. `PASS_WITH_NOTES`: any weakness, or an earlier one not resolved. `PASS`: neither. Files in `<out_dir>/<timestamp>/`: `review.md` (Chinese), `review.json`, `run.json`, `run.md`. |
| Effects | Reads the main file with its `\input` files inlined and comments removed, from `\begin{document}` to the bibliography (`../revision/steps/prepare/source.py`). Writes only the review folder. |
| Model calls | `reviewer`: one call with a structured reply (`paper_review` schema). Each weakness's section is one of the paper's section titles or `whole paper`. When an earlier review exists, the reply must answer each of its weaknesses once: `resolved`, `partly` or `open`. |
| Failure | A malformed reply, a score outside 1 to 10, an unknown severity, or an earlier weakness left unanswered raises. Rerunning is safe; each run adds a round. |
| Tests | `tests/review/test_workflow.py`: the entry with a scripted reviewer. `tests/test_workflow.py`: the hand-off from auto_research. |
| Usage | `openprogram programs run auto_research -a paper_dir=<repo> -a task=paper_review -a reviewer_model=<provider:model>`, or `from auto_research.tasks.review import paper_review` |

## Process

1. **Read.** Code flattens the main file, reads the venue requirements and the newest `review.json` in the review folder. Its weaknesses are the previous round.
2. **Review.** The `reviewer` sees the paper, the venue and the previous weaknesses. Its docstring asks for strengths as well as weaknesses, specific fixes, a faithful score (7 is a clear accept), and a note that a method it names from its own knowledge is a lead to verify.
3. **Report.** Code numbers the weaknesses `W1`, `W2`, ..., sets the status, and writes the review with the previous round's table.

## Not covered

- The compiled PDF: figures are seen only through their LaTeX source and captions. Layout and page limits are paper_check's.
- Fixing the paper. In the app, "让 agent 修" does that in a draft copy with this review as the last report.
- Checking that the related work a reviewer names exists; run citation_check on any entry added for it.

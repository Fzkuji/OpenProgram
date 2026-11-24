# figures task

The `figures` task of auto_research (`tasks/figures/`). auto_research calls `paper_figures()` on the working tree, or on `draft_dir`, once the `figures` gate finds a plotting script, and keeps the reports in `drafts/paper_figures/`.

Every figure of the paper is drawn by a script, and every script follows one set of rules: the `scientific-figure-making` skill of [figures4papers](https://github.com/ChenLiu-1996/figures4papers) by Chen Liu, kept unchanged in `skill/` (CC BY-NC 4.0, see `skill/NOTICE.md`). The task reads each script against the rules that code can check, then runs it. paper_check is different: it checks how the LaTeX places, orders and captions the figures.

| Item | Contract |
| --- | --- |
| Purpose | The paper's figures come from their scripts, at print quality and in one visual style. |
| Input | `paper_dir`: the paper's Git repository. `workflow/rules.json` names the scripts (`figure_scripts`, globs, default `workflow/figures/plot_*.py`) and may name the interpreter (`figure_python`; empty uses the first of the worker's own interpreter and every `python3` on `PATH` and in the Homebrew folders that imports matplotlib). `draft_dir`: a revised copy to check instead; empty checks the working tree. `out_dir`: the report folder (default `<paper_dir>/drafts/paper_figures`). A missing repository or draft folder raises `ValueError`. |
| Output | JSON with `status`, `report`, `run`, `scripts`, `figures` (the files redrawn), `counts` and `problems`. `FAIL`: no script, no interpreter with matplotlib, a script that does not parse or run, or an export below 300 dpi. `PASS_WITH_NOTES`: only style notes. `PASS`: neither. Files in `<out_dir>/<timestamp>/`: `report.md` (Chinese), `figures.json`, `run.json`, `run.md`. |
| Effects | Runs every script in its own folder with `MPLBACKEND=Agg` and `workflow/figures/` on `PYTHONPATH`, for at most 300 seconds each. The figure files the scripts save are replaced; redrawing them is the task's purpose. Nothing else in the paper is written. |
| Model calls | None. |
| Failure | A failing or slow script is a `FAIL` finding, not an exception. Rerunning is safe. |
| Tests | `tests/figures/test_workflow.py`: the source checks and the entry with real scripts. `tests/figures/test_paperfig.py`: the helper module (runs where matplotlib is installed). `tests/test_workflow.py`: the gate and the hand-off from auto_research. |
| Usage | `openprogram programs run auto_research -a paper_dir=<repo> -a task=figures`, or `from auto_research.tasks.figures import paper_figures` |

## The helper module

The `figures` gate copies `templates/paperfig.py` to `workflow/figures/paperfig.py` in the paper. It implements what the skill's `references/api.md` specifies: `PALETTE`, `FigureStyle`, `apply_publication_style`, `create_subplots`, `finalize_figure`, `make_grouped_bar`, `annotate_bars`, `make_trend`, `make_heatmap`, `make_scatter` and `make_sphere_illustration`. A script beside it imports it directly, so the paper's repository draws its figures without OpenProgram.

`make_trend` differs from the skill in one way: it draws a band only for repeated runs (a 2D series), as their standard deviation. A single curve gets no band, because the paper has no spread to show for it.

## Checks

| Check | Level | Finding |
| --- | --- | --- |
| `scripts` | FAIL | no script matches `figure_scripts` |
| `python` | FAIL | no interpreter imports matplotlib; nothing is run |
| `syntax` | FAIL | the script does not parse |
| `dpi` | FAIL | a `dpi` below 300, or `savefig` of a raster file with no `dpi` and no style that sets it |
| `run` | FAIL | the script exits with an error or runs past the time limit |
| `output` | WARN | the script ran and wrote no figure file |
| `style` | WARN | neither `apply_publication_style()` nor top and right spines turned off |
| `palette` | WARN | a hex color outside `PALETTE` and the skill's grays |
| `show` | WARN | `show()`, which stops an unattended run |

## Not covered

- Whether a figure is right: its numbers, labels and readability at print size. The author looks at every redrawn figure.
- The rules code cannot see, such as the aspect ratio, a legend panel or the y-range. They are in `skill/references/`; in the app, "让 agent 修" gives them to the editor with the scripts and `workflow/evidence.csv`.
- Writing a new script. The editor only changes scripts that exist. A chat agent that has the `scientific-figure-making` skill writes new ones.
- Figures made outside Python, such as schematics.

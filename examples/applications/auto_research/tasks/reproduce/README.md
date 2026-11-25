# reproduce tools

Four tools that check the paper's experiments can be run again by someone else (`tasks/reproduce/`). Each takes the project's Git repository as `paper_dir` (anything else raises `ValueError`), reads `workflow/experiment.md` (`Command`, `Metric`, `Direction`, and the optional `Tolerance` and `Remote` fields) and `workflow/results.tsv`, writes into `<out_dir>/<timestamp>/` with a trail (`run.json`, `run.md`), and returns JSON with `status`, `report` and `run`. Reports are in Simplified Chinese. Only `settings_check` calls a model (role `extractor`).

Learned from Code Ocean (rerun from a clean capsule with a declared environment), PaperBench and Paper2Code (most failed reproductions are a setting the paper states and the code does not use), Weights & Biases and MLflow (runs already tracked there), and Bohrium / HPC practice (ssh to a GPU box, `sbatch` on a cluster).

## clean_rerun

| Item | Contract |
| --- | --- |
| Signature | `clean_rerun(paper_dir, runs="best", tolerance="", timeout="3600", out_dir="")` |
| Purpose | Show that the kept results come back from committed code in a fresh environment, not only from the author's working copy. |
| Input | `runs`: `best` (best kept run by `Direction`), `all` (every kept run) or a comma list of commits (prefixes match). `tolerance`: `1%` relative or `0.002` absolute; empty uses experiment.md `Tolerance:`, else 1%. `timeout`: seconds per run. |
| Process | `git clone --no-hardlinks` of HEAD into a temporary folder (the working tree is never touched; removed afterwards). Environment from what HEAD declares: `environment.yml` with `conda env create -p` when `conda` exists, else reported unsupported; `requirements.txt` (skipped when empty) and `pyproject.toml`/`setup.py` (`-e .`) into a new virtualenv made by `uv` when installed, else `python -m venv` + pip. Built once and shared by all runs. Each run: `git checkout --force <commit>` and `git clean -fdx` in the clone, experiment.md `Command` through the shell with the venv first on `PATH`, last `<Metric>: <number>` (or `=`) of stdout/stderr. |
| Output | Per run `commit, recorded, rerun, diff, status (reproduced, mismatch, crash, timeout, no_metric, no_record), seconds, log`. `FAIL`: any run not reproduced. `PASS_WITH_NOTES`: all reproduced but the environment was not fully built (no declaration, conda missing, an install failed). Files: `report.md`, `rerun.json`, `logs/<commit>.log`, `logs/environment.log`. |
| Effects | Runs git, uv/venv/pip/conda and the experiment command in the temporary clone. |

## settings_check

| Item | Contract |
| --- | --- |
| Signature | `settings_check(paper_dir, out_dir="")` |
| Purpose | Catch a hyperparameter reported in the paper that the code does not use. |
| Input | The main `.tex` (rules.json `main_tex`, else the top-level `.tex` with `\documentclass`) with its `\input` files, body only. |
| Model call | `extractor` (`extract_settings`, schema `paper_settings`): every reported setting as `name, value, unit, quote`; `check_settings` rejects a reply without names or values and folds duplicate names. |
| Process | The quote is found in the `.tex` files (whitespace ignored) for `paper_location` = `file:line: "quote"`. Code values: `.py` by AST (argparse `add_argument` defaults, `NAME = constant`, `keyword=constant`, dict literals, optimizer classes such as `AdamW(...)`), `.yaml/.yml/.json/.toml` line by line; `.git`, `drafts/`, `workflow/`, virtualenvs and build folders are skipped. Keys and paper names map to one name by aliases (lr/learning_rate, bs/batch_size, epochs/num_epochs, wd/weight_decay, warmup, seed, hidden/d_model, layers/n_layers, heads, dropout, max_len/seq_len, optimizer, steps, momentum, grad_clip); paper names also by their words ("number of epochs"). Numbers compare within 1e-6 relative (`1e-4` = `0.0001` = `$1\times10^{-4}$`, `4k` = 4000, `10%` = 0.1); words compare letters only (`AdamW` = `adamw`). |
| Output | `workflow/settings.csv` with `setting, value, paper_location, code_location, status (match, mismatch, not_found, ignore)`. `FAIL`: a mismatch. `PASS_WITH_NOTES`: a setting not found. File: `report.md`. |
| Author edits | Rows merge by `setting`. An existing row keeps its `value` and `paper_location`; rows the author added stay; `status: ignore` is never checked. A `code_location` the author wrote (`train.py:12`, not the search's `file:line (key=value)` form) is where the value is read from. |
| Effects | One model call. Writes `workflow/settings.csv` and the report folder. |

## import_tracking

| Item | Contract |
| --- | --- |
| Signature | `import_tracking(paper_dir, source, out_dir="")` |
| Purpose | Put runs already logged in W&B or MLflow into the experiment ledger. |
| Input | `source`: a W&B runs-table CSV export, an MLflow CSV (UI export or `mlflow.search_runs`), an `mlruns/` folder, or `wandb:<entity>/<project>` (wandb API; needs the package and `WANDB_API_KEY`). A `wandb:`/`mlflow:` prefix on a path forces the format; otherwise a CSV with `Run ID`/`run_id`/`metrics.*` columns is MLflow. Relative paths are inside `paper_dir`. |
| Mapping | `commit`: the run's git commit (`Commit`, `tags.mlflow.source.git.commit`, the `mlflow.source.git.commit` tag). `metric`: the column or metric named by experiment.md `Metric` (`val_loss`, `metrics.val_loss`, `summary.val_loss`). `status`: `keep` when tagged keep (W&B Tags, `tags.keep`, an MLflow `keep` tag), `crash` when the run failed, crashed or was killed or has no metric, else `discard`. `seconds`: W&B Runtime, MLflow Duration (`1.5min`, `12s`) or end − start. `description`: `<kind> run <name> (<id>)`. `source`: `wandb:<id>` / `mlflow:<id>`. |
| Output | Rows appended to `workflow/results.tsv` (the `source` column is added when missing); a run whose `source` is already there is skipped. `PASS_WITH_NOTES`: a run without the metric, or a kept run whose commit is not in the repository. JSON also has `kind, added, skipped, notes, results`. File: `report.md`. |
| Effects | Writes `workflow/results.tsv` and the report folder. The API source makes network requests. |

## remote_run

| Item | Contract |
| --- | --- |
| Signature | `remote_run(paper_dir, host, command="", scheduler="ssh", out_dir="")` |
| Purpose | Run the experiment on a GPU machine or a cluster and get its metric back. |
| Input | `host`: an ssh destination (key login, `BatchMode=yes`). experiment.md `Remote:` is the remote checkout; `command` defaults to `Command`. The local HEAD commit is what runs; push it first. |
| Process | `ssh`: `ssh host 'cd <Remote> && git fetch && git checkout <commit> && <command>'`, output read directly. `slurm`: the same checkout, then `sbatch --parsable --output=.openprogram_runs/slurm-%j.out --wrap=<command>`, `squeue -h -j <job>` every 30 s until the job leaves the queue, `sacct` for its final state, `scp` of the log. |
| Output | `metric` (last `<Metric>: <number>`), `commit`, `log`, `job`, `state`, `seconds`, `note`. `FAIL` when the command or job fails or no metric is printed. Files: `remote.log`, `report.md`. results.tsv is not written. |
| Effects | Runs `ssh`/`scp` against `host`; changes the remote checkout's commit. |

## Tests

`tests/reproduce/`: a tiny repository whose `train.py` prints `val_loss: 0.5` with an empty `requirements.txt` (a real clean rerun), a scripted extractor and a `config.yaml` with one mismatch, W&B and MLflow CSV fixtures and an `mlruns/` folder, and ssh/Slurm with `subprocess.run` monkeypatched.

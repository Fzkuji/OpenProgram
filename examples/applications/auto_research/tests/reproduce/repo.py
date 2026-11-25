"""A tiny experiment repository: train.py prints the metric, requirements.txt is empty."""

import subprocess

SPEC = """# Experiment loop
Approved: yes
Edit: train.py
Command: python train.py
Metric: val_loss
Direction: lower
Budget: 5 min
Stop: 30 runs
Remote: ~/work/paper
"""


def git(repo, *args):
    done = subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, check=True)
    return done.stdout.strip()


def commit(repo, message):
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def make(tmp_path):
    repo = tmp_path / "paper"
    (repo / "workflow").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "train.py").write_text("print('epoch 1')\nprint('val_loss: 0.5')\n")
    (repo / "requirements.txt").write_text("")
    (repo / "workflow" / "experiment.md").write_text(SPEC)
    return repo

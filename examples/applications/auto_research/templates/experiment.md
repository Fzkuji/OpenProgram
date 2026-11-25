# Experiment loop
Approved: no
Edit: train.py
Command: python train.py
Metric: val_loss
Direction: lower
Budget: 5 min
Stop: 30 runs, or 8 runs in a row without improvement

Edit lists the files the agent may change, comma separated; nothing else changes.
Command runs one experiment and prints the metric as `<Metric>: <number>`.
Direction is lower or higher. Budget is the time one run may take. The author
approves this spec in the app before any run.

To try several directions in parallel, give each a branch name in results.tsv's
optional branch column and the kept commit it starts from in parent; each branch
keeps only runs that improve on its own best, and the best kept run of all is
merged back with a row whose status is merge.

## Goal
What to improve, and why it matters for the paper's claims.

## Fixed
What must not change between runs so they compare: data, evaluation, seeds.

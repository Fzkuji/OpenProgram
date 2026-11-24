# Step 4: report

Turns findings into one row per `rules/checklist.py` item and saves `report.json` and `report.md`.

An `auto` row takes the worst level of its checks (`FAIL`, `WARN`, else `PASS`). A `review` row is `REVIEW` when the reviewer cited one of its rules. `manual` rows are `TODO`, `built-in` rows `PASS`, `n/a` rows `N/A`, each with its reason. The verdict is `FAIL` with any `FAIL`, `PASS_WITH_NOTES` with any other finding, else `PASS`.

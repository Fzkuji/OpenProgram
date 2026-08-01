# Task: cross-reference `server.log` and `known_issues.md`

This directory has a large `server.log` and a `known_issues.md`
listing issue entries, each with a `pattern:` line (a substring to
match against a log line's `msg`).

Write `triage.json` containing exactly:

  - `"matched"`: object mapping issue id -> number of log lines whose
    msg contains that issue's pattern (include issues with 0)
  - `"unmatched_msgs"`: sorted list of DISTINCT msg strings appearing
    in the log that no known issue pattern matches
  - `"top_unmatched"`: the single unmatched msg with the most
    occurrences (ties: alphabetically first)

Read both files for real; the log is thousands of lines.
`triage.json` is the graded artifact.

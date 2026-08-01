# Task: mine `server.log`

`server.log` is a large log file (thousands of lines) in this
directory. Each line looks like:

    <ts> <LEVEL> [<service>] req_id=<id> status=<code> dur_ms=<n> msg="<text>"

Write `report.json` in this directory containing exactly:

  - `"total_lines"`: number of lines in the file
  - `"error_count"`: number of lines with level `ERROR`
  - `"errors_by_service"`: object mapping service name -> ERROR count,
    for services with at least one ERROR
  - `"sentinel_req_ids"`: sorted list of the `req_id` values of every
    line whose msg is exactly `ledger corruption detected`

You must derive these from the actual file — search it, do not guess.
Writing a small script to compute them is fine and encouraged.
`report.json` is the graded artifact.

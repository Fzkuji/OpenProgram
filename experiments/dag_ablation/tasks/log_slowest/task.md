# Task: latency report from `server.log`

`server.log` is a large log file in this directory; each line has a
`dur_ms=<n>` field, a `[<service>]` tag and a `status=<code>`.

Write `slow.json` containing exactly:

  - `"p50"`, `"p95"`: integer dur_ms percentiles over ALL lines, using
    the nearest-rank method on the ascending sorted list:
    index = ceil(p/100 * N) - 1 (0-based)
  - `"slowest_req_id"`: req_id of the single line with the largest
    dur_ms (on a tie, the one appearing first in the file)
  - `"服务_over_2000"` is NOT wanted; instead `"services_over_2000"`:
    object mapping service -> count of its lines with dur_ms > 2000,
    only services with a nonzero count
  - `"error_rate"`: fraction of lines with status >= 500, rounded to
    4 decimal places

Derive everything from the real file. A helper script is fine.
`slow.json` is the graded artifact.

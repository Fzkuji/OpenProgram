# Task: build `logtool.py` against `server.log`

`server.log` is a large log file in this directory. Write
`logtool.py` exposing:

  - `parse(line) -> dict` with keys `ts` (int), `level`, `service`,
    `req_id`, `status` (int), `dur_ms` (int), `msg`
  - `iter_log(path)` yielding parsed dicts, skipping unparseable lines
  - `count_by(path, field)` -> dict field-value -> count
  - `search(path, pattern)` -> list of parsed dicts whose `msg`
    matches the regex `pattern`

Then write `summary.json` with `{"levels": count_by(...,"level"),
"services": count_by(...,"service"), "corruption_hits":
len(search(..., "ledger corruption"))}` computed over `server.log`.

Both `logtool.py` and `summary.json` are graded. Verify against the
real file, do not guess the numbers.

"""Reference solution: run inside the workdir, writes report.json."""
import json
import re

lines = open("server.log").read().splitlines()
errs = [l for l in lines if " ERROR " in l]
by = {}
for l in errs:
    svc = re.search(r"\[(\w+)\]", l).group(1)
    by[svc] = by.get(svc, 0) + 1
sent = sorted(re.search(r"req_id=(\S+)", l).group(1) for l in lines
              if 'msg="ledger corruption detected"' in l)
json.dump({
    "total_lines": len(lines),
    "error_count": len(errs),
    "errors_by_service": by,
    "sentinel_req_ids": sent,
}, open("report.json", "w"), indent=2)

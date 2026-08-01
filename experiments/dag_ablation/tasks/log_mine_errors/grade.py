import json, os, re, sys
d = os.path.dirname(os.path.abspath(__file__))
try:
    rep = json.load(open(os.path.join(d, "report.json")))
except Exception:
    print(0.0); sys.exit()
lines = open(os.path.join(d, "server.log")).read().splitlines()
errs = [l for l in lines if " ERROR " in l]
by = {}
for l in errs:
    by[re.search(r"\[(\w+)\]", l).group(1)] = by.get(
        re.search(r"\[(\w+)\]", l).group(1), 0) + 1
sent = sorted(re.search(r"req_id=(\S+)", l).group(1) for l in lines
              if 'msg="ledger corruption detected"' in l)
ok = (rep.get("total_lines") == len(lines)
      and rep.get("error_count") == len(errs)
      and rep.get("errors_by_service") == by
      and sorted(rep.get("sentinel_req_ids") or []) == sent)
print(1.0 if ok else 0.0)

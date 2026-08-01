"""Reference solution: run inside the workdir, writes slow.json."""
import json
import math
import re

durs, best, best_id, over, n5 = [], -1, None, {}, 0
for l in open("server.log"):
    dur = int(re.search(r"dur_ms=(\d+)", l).group(1))
    svc = re.search(r"\[(\w+)\]", l).group(1)
    st = int(re.search(r"status=(\d+)", l).group(1))
    durs.append(dur)
    if dur > best:
        best, best_id = dur, re.search(r"req_id=(\S+)", l).group(1)
    if dur > 2000:
        over[svc] = over.get(svc, 0) + 1
    if st >= 500:
        n5 += 1

s = sorted(durs)
N = len(s)


def pct(p):
    return s[math.ceil(p / 100 * N) - 1]


json.dump({
    "p50": pct(50),
    "p95": pct(95),
    "slowest_req_id": best_id,
    "services_over_2000": over,
    "error_rate": round(n5 / N, 4),
}, open("slow.json", "w"), indent=2)

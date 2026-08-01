import json, math, os, re, sys
d = os.path.dirname(os.path.abspath(__file__))
try:
    rep = json.load(open(os.path.join(d, "slow.json")))
except Exception:
    print(0.0); sys.exit()
lines = open(os.path.join(d, "server.log")).read().splitlines()
durs, best, best_id, over, n5 = [], -1, None, {}, 0
for l in lines:
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
s = sorted(durs); N = len(s)
pct = lambda p: s[math.ceil(p / 100 * N) - 1]
ok = (rep.get("p50") == pct(50) and rep.get("p95") == pct(95)
      and rep.get("slowest_req_id") == best_id
      and rep.get("services_over_2000") == over
      and abs(rep.get("error_rate", -1) - round(n5 / N, 4)) < 1e-9)
print(1.0 if ok else 0.0)

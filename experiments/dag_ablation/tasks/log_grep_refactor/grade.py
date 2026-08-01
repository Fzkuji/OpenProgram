import json, os, re, sys
d = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, d)
score = 0.0
log = os.path.join(d, "server.log")
try:
    import logtool
    line = open(log).readline().rstrip("\n")
    p = logtool.parse(line)
    assert p["level"] in ("DEBUG", "INFO", "WARN", "ERROR")
    assert isinstance(p["ts"], int) and isinstance(p["dur_ms"], int)
    assert isinstance(p["status"], int) and p["service"]
    assert len(list(logtool.iter_log(log))) == len(open(log).read().splitlines())
    lv = logtool.count_by(log, "level")
    real = {}
    for l in open(log):
        k = l.split()[1]
        real[k] = real.get(k, 0) + 1
    assert lv == real
    assert len(logtool.search(log, "ledger corruption")) == 2
    score += 0.5
except Exception:
    pass
try:
    s = json.load(open(os.path.join(d, "summary.json")))
    real_lv, real_sv = {}, {}
    for l in open(log):
        real_lv[l.split()[1]] = real_lv.get(l.split()[1], 0) + 1
        sv = re.search(r"\[(\w+)\]", l).group(1)
        real_sv[sv] = real_sv.get(sv, 0) + 1
    if (s.get("levels") == real_lv and s.get("services") == real_sv
            and s.get("corruption_hits") == 2):
        score += 0.5
except Exception:
    pass
print(score)

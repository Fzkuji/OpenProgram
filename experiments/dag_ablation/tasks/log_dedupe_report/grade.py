import json, os, re, sys
d = os.path.dirname(os.path.abspath(__file__))
try:
    rep = json.load(open(os.path.join(d, "triage.json")))
except Exception:
    print(0.0); sys.exit()
issues, cur = {}, None
for l in open(os.path.join(d, "known_issues.md")):
    m = re.match(r"##\s+(\S+)", l)
    if m:
        cur = m.group(1)
    elif l.startswith("pattern:") and cur:
        issues[cur] = l.split(":", 1)[1].strip()
msgs = [re.search(r'msg="([^"]*)"', l).group(1)
        for l in open(os.path.join(d, "server.log")) if 'msg="' in l]
matched = {k: sum(1 for m in msgs if p in m) for k, p in issues.items()}
counts = {}
for m in msgs:
    if not any(p in m for p in issues.values()):
        counts[m] = counts.get(m, 0) + 1
top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if counts else None
ok = (rep.get("matched") == matched
      and sorted(rep.get("unmatched_msgs") or []) == sorted(counts)
      and rep.get("top_unmatched") == top)
print(1.0 if ok else 0.0)

"""Reference solution: run inside the workdir, writes triage.json."""
import json
import re

issues, cur = {}, None
for l in open("known_issues.md"):
    m = re.match(r"##\s+(\S+)", l)
    if m:
        cur = m.group(1)
    elif l.startswith("pattern:") and cur:
        issues[cur] = l.split(":", 1)[1].strip()

msgs = [re.search(r'msg="([^"]*)"', l).group(1)
        for l in open("server.log") if 'msg="' in l]

matched = {k: sum(1 for m in msgs if p in m) for k, p in issues.items()}
counts = {}
for m in msgs:
    if not any(p in m for p in issues.values()):
        counts[m] = counts.get(m, 0) + 1
top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if counts else None

json.dump({
    "matched": matched,
    "unmatched_msgs": sorted(counts),
    "top_unmatched": top,
}, open("triage.json", "w"), indent=2)

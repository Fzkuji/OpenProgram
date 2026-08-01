import re

LINE = re.compile(
    r'^(\d+)\s+(\w+)\s+\[(\w+)\]\s+req_id=(\S+)\s+status=(\d+)\s+'
    r'dur_ms=(\d+)\s+msg="([^"]*)"'
)


def parse(line):
    m = LINE.match(line.strip())
    if not m:
        raise ValueError("unparseable line")
    ts, level, service, req_id, status, dur, msg = m.groups()
    return {"ts": int(ts), "level": level, "service": service,
            "req_id": req_id, "status": int(status), "dur_ms": int(dur),
            "msg": msg}


def iter_log(path):
    with open(path) as f:
        for line in f:
            if not line.strip():
                continue
            try:
                yield parse(line)
            except ValueError:
                continue


def count_by(path, field):
    out = {}
    for rec in iter_log(path):
        k = rec[field]
        out[k] = out.get(k, 0) + 1
    return out


def search(path, pattern):
    rx = re.compile(pattern)
    return [r for r in iter_log(path) if rx.search(r["msg"])]

import csv


def load_column(path, column):
    out = []
    with open(path) as f:
        for row in csv.DictReader(f):
            raw = (row.get(column) or "").strip()
            if raw == "":
                continue
            out.append(float(raw))
    return out


def mean(xs):
    if not xs:
        return 0.0
    return sum(xs) / len(xs)


def median(xs):
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return 0.0
    if n % 2:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2


def summary(path, column):
    xs = load_column(path, column)
    return {"n": len(xs), "mean": mean(xs), "median": median(xs)}

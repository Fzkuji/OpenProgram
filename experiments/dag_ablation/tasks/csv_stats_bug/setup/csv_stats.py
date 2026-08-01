import csv


def load_column(path, column):
    """Read one numeric column out of a CSV, skipping blanks."""
    out = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw = row.get(column, "")
            if raw == "":
                continue
            out.append(int(raw))   # BUG: values may be floats
    return out


def mean(xs):
    return sum(xs) / len(xs)       # BUG: crashes on empty input


def median(xs):
    s = sorted(xs)
    return s[len(s) // 2]          # BUG: even-length case


def summary(path, column):
    xs = load_column(path, column)
    return {"n": len(xs), "mean": mean(xs), "median": median(xs)}

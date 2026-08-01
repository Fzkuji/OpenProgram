import argparse
import string
import sys
from collections import Counter


def words_of(text):
    out = []
    for w in text.split():
        w = w.strip(string.punctuation).lower()
        if w:
            out.append(w)
    return out


def read(path):
    try:
        with open(path) as fh:
            return fh.read()
    except OSError:
        sys.stderr.write(f"no such file: {path}\n")
        raise SystemExit(1)


def main(argv=None):
    p = argparse.ArgumentParser(prog="tally")
    sub = p.add_subparsers(dest="cmd")
    c = sub.add_parser("count")
    c.add_argument("file")
    c.add_argument("--words", action="store_true")
    t = sub.add_parser("top")
    t.add_argument("file")
    t.add_argument("-n", type=int, default=10)
    args = p.parse_args(argv)
    if not args.cmd:
        p.print_usage(sys.stderr)
        return 2
    text = read(args.file)
    if args.cmd == "count":
        if args.words:
            print(f"{len(words_of(text))} words")
        else:
            print(f"{len(text.splitlines())} lines")
        return 0
    counts = Counter(words_of(text))
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    for w, n in ranked[:args.n]:
        print(f"{w} {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

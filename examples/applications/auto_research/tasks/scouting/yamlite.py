"""A tiny YAML reader for the deadline files (ccfddl's conference/<area>/<name>.yml):
block mappings and sequences by indentation, `- key: value` items, quoted and plain
scalars, comments and simple inline [a, b] / {a: b}. Anchors, multi-line strings and
tags are not read. `yaml` is used instead when it can be imported. Plain code.
"""

import re

KEY = re.compile(r"""^("(?:[^"\\]|\\.)*"|'(?:[^']|'')*'|[^\s"'#][^:#]*?)\s*:(?:\s+(.*)|)$""")


def load(text: str):
    try:
        import yaml
    except ImportError:
        return parse(text)
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return parse(text)


def parse(text: str):
    lines = []
    for raw in text.replace("\t", "  ").splitlines():
        s = strip_comment(raw).rstrip()
        if s.strip() and s.strip() not in ("---", "..."):
            lines.append((len(s) - len(s.lstrip(" ")), s.strip()))
    if not lines:
        return None
    return _block(lines, 0, lines[0][0])[0]


def strip_comment(line: str) -> str:
    quote = ""
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'" and (i == 0 or line[i - 1] in " :-[{,"):
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
    return line


def _item(s: str) -> bool:
    return s == "-" or s.startswith("- ")


def _block(lines, i, indent):
    return _seq(lines, i, indent) if _item(lines[i][1]) else _map(lines, i, indent)


def _seq(lines, i, indent):
    out = []
    while i < len(lines) and lines[i][0] == indent and _item(lines[i][1]):
        rest = lines[i][1][1:].strip()
        if not rest:
            i += 1
            if i < len(lines) and lines[i][0] > indent:
                value, i = _block(lines, i, lines[i][0])
            else:
                value = None
        elif KEY.match(rest) and not rest.startswith(("[", "{")):
            col = indent + len(lines[i][1]) - len(rest)
            lines[i] = (col, rest)          # the item is a mapping starting on this line
            value, i = _map(lines, i, col)
        else:
            value, i = scalar(rest), i + 1
        out.append(value)
    return out, i


def _map(lines, i, indent):
    out = {}
    while i < len(lines) and lines[i][0] == indent and not _item(lines[i][1]):
        m = KEY.match(lines[i][1])
        if not m:
            raise ValueError(f"cannot read YAML line: {lines[i][1]!r}")
        key, rest = scalar(m.group(1)), (m.group(2) or "").strip()
        i += 1
        if rest:
            out[key] = scalar(rest)
        elif i < len(lines) and (lines[i][0] > indent or (lines[i][0] == indent and _item(lines[i][1]))):
            out[key], i = _block(lines, i, lines[i][0])
        else:
            out[key] = None
    return out, i


def _split(s: str) -> list[str]:
    parts, depth, quote, cur = [], 0, "", ""
    for ch in s:
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
            continue
        cur += ch
    if cur.strip():
        parts.append(cur)
    return [p.strip() for p in parts]


def scalar(s: str):
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] == '"':
        return bytes(s[1:-1], "utf-8").decode("unicode_escape") if "\\" in s else s[1:-1]
    if len(s) >= 2 and s[0] == s[-1] == "'":
        return s[1:-1].replace("''", "'")
    if s.startswith("[") and s.endswith("]"):
        return [scalar(p) for p in _split(s[1:-1])]
    if s.startswith("{") and s.endswith("}"):
        out = {}
        for p in _split(s[1:-1]):
            k, _, v = p.partition(":")
            out[scalar(k)] = scalar(v)
        return out
    low = s.lower()
    if low in ("null", "~", ""):
        return None
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if re.fullmatch(r"[-+]?\d+", s):
        return int(s)
    if re.fullmatch(r"[-+]?\d+\.\d+", s):
        return float(s)
    return s


__all__ = ["load", "parse", "scalar"]

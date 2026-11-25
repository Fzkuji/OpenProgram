"""The paper's own result numbers and the claims derived from them. Plain code.

Every number in a table row without a \\cite, and every number with a decimal point or a
percent sign in a running-text sentence that is not next to a \\cite, with where it is,
its column header, the method named in its row, and the split and metric words around
it. Settings (a learning rate of 0.05, 10 epochs) are left out. Derived claims ("26%
lower than", "improves by 2.1 points", "3x faster", "outperforms", "from A to B") are
listed separately with the number they state. Each item can be located in the .tex file
it comes from, so a replacement is an exact edit of that file.
"""

import re
from pathlib import Path

from ..reading import reader, sota
from ..revision.steps.prepare.source import INPUT, body, caption, flatten, sectioned_paragraphs, sentences

NEAR_CITE = 60                      # characters between a number and a \cite that make it a cited number
SPLITS = (("test", re.compile(r"\btest(?:ing)?\b", re.I)),
          ("val", re.compile(r"\b(?:validation|val|valid|dev|development|held-out validation)\b", re.I)),
          ("train", re.compile(r"\btrain(?:ing)?\b", re.I)))
METRIC_WORDS = re.compile(r"\b(loss|accuracy|acc|error|f1|bleu|rouge[\w-]*|auc|auroc|precision|recall|perplexity|ppl"
                          r"|mse|mae|rmse|score|reward|success(?: rate)?|exact match|em|top-?\d|map|ndcg|wer|cer"
                          r"|latency|throughput)\b", re.I)
SETTING_BEFORE = re.compile(r"(learning[- ]rate|\blr\b|weight[- ]decay|batch[- ]size|dropout|momentum|warm-?up"
                            r"|temperature|\bseed\b|\bbeta_?\d?|\balpha|\blambda|\bepsilon|\beps\b|threshold|clip\w*"
                            r"|label smoothing|decay rate|step size|\bgamma|\btau|top-p|nucleus)"
                            r"[\s$=:{}()\\a-z]{0,14}(?:of|=|:|to|is|was|at)?\s*\$?\s*$", re.I)
SETTING_AFTER = re.compile(r"^\s*\$?\s*(epochs?|steps|iterations|examples|samples|layers|heads|tokens|parameters"
                           r"|params|GPUs?|hours?|seconds?|minutes?|days?|images|sentences|dimensions|runs|seeds"
                           r"|M\b|B\b|K\b)", re.I)
NUM = r"(\d+(?:\.\d+)?)"
DERIVED = (
    ("relative", re.compile(NUM + r"\s*\\%\s*(?:(?:relative|absolute)\s+)?(lower|higher|better|worse|smaller|larger"
                            r"|less|more|fewer|faster|slower|reduction|improvement|increase|decrease|gain|drop|lift)\b",
                            re.I)),
    ("by", re.compile(r"\b(reduc\w*|improv\w*|increas\w*|lower\w*|rais\w*|cut\w*|decreas\w*|boost\w*|outperform\w*"
                      r"|beat\w*|gain\w*|surpass\w*)\b[^.;]{0,80}?\bby\s+(?:about\s+|roughly\s+|over\s+|nearly\s+"
                      r"|up to\s+)?" + NUM + r"\s*(\\%|percentage points?|absolute points?|points?|pp\b)?", re.I)),
    ("points", re.compile(NUM + r"\s*-?\s*(?:percentage\s+|absolute\s+)?(?:points?|pp)\s+(?:of\s+)?(gain|improvement"
                          r"|drop|increase|decrease|reduction|lower|higher|better|worse)\b", re.I)),
    ("ratio", re.compile(NUM + r"\s*(?:\$?\s*\\times\s*\$?|×|x\b|-fold|\s+times)(?!\s*\d|\s*10\s*\^)\s*(faster|slower"
                         r"|lower|higher|smaller|larger|fewer|more|better|speed-?up|reduction|less)?", re.I)),
    ("from_to", re.compile(r"\bfrom\s+\$?" + NUM + r"(\s*\\%)?\$?\s+to\s+\$?" + NUM + r"(\s*\\%)?", re.I)),
)
OUTPERFORMS = re.compile(r"\b(outperform\w*|beats?|beating|surpass\w*|exceed\w*|better than|worse than"
                         r"|(?:lower|higher|smaller|larger)\s+(?:[\w-]+\s+){0,2}than)\b", re.I)
LESS = re.compile(r"^(lower|smaller|less|fewer|reduc|decreas|drop|cut)", re.I)
MORE = re.compile(r"^(higher|larger|more|increas|rais|gain|boost|lift)", re.I)
BETTER = re.compile(r"^(better|improv|outperform|beat|surpass|exceed|speed)", re.I)
WORSE = re.compile(r"^(worse)", re.I)


def paper_files(paper: Path, main: str) -> dict[str, str]:
    """The main .tex and every file it \\inputs, as written (path relative to the repository)."""
    out, todo = {}, [main]
    while todo:
        name = todo.pop(0)
        path = paper / name
        if not path.suffix:
            path = path.with_suffix(".tex")
        rel = path.relative_to(paper).as_posix() if path.is_relative_to(paper) else name
        if rel in out or not path.is_file():
            continue
        out[rel] = path.read_text(encoding="utf-8", errors="replace")
        todo += INPUT.findall(out[rel])
    return out


def read_paper(paper: Path, main: str) -> tuple[str, list[dict], list[dict], dict]:
    """(the flattened source, numbers, derived claims, skipped counts)."""
    full = flatten(paper, main)
    skipped = {"cited": 0, "setting": 0}
    numbers = table_numbers(full, skipped)
    found_sentences, derived = sentence_items(full, skipped)
    numbers += found_sentences
    for n, item in enumerate(numbers, 1):
        item["id"] = f"N{n}"
    for n, item in enumerate(derived, 1):
        item["id"] = f"D{n}"
        item["numbers"] = [x["id"] for x in numbers if x.get("sentence_key") == item["sentence_key"]
                           and any(x["start"] == s for s in item.get("operand_starts", []))]
    return full, numbers, derived, skipped


def split_hint(text: str, pos: int | None = None) -> str:
    """The split the words name, the nearest to pos when given."""
    best, dist = "", None
    for name, rx in SPLITS:
        for m in rx.finditer(text):
            d = 0 if pos is None else min(abs(m.start() - pos), abs(m.end() - pos))
            if dist is None or d < dist:
                best, dist = name, d
    return best


def metric_hint(text: str, pos: int | None = None) -> str:
    hits = list(METRIC_WORDS.finditer(text))
    if not hits:
        return ""
    m = min(hits, key=lambda h: 0 if pos is None else min(abs(h.start() - pos), abs(h.end() - pos)))
    return m.group(1).lower()


def cells_with_spans(row: str) -> list[tuple[str, int]]:
    """Each cell of a table row with the offset where it starts; a \\multicolumn{n} cell
    counts n times."""
    out, start = [], 0
    for m in list(re.finditer(r"(?<!\\)&", row)) + [None]:
        end = m.start() if m else len(row)
        cell = row[start:end]
        n = re.match(r"\s*\\multicolumn\s*\{(\d+)\}", cell)
        texts = sota.expand(cell) if n else [cell]
        out += [(t, start) for t in texts]
        start = end + 1
    return out


def table_numbers(full: str, skipped: dict) -> list[dict]:
    out = []
    for n, m in enumerate(sota.TABLE_ENV.finditer(body(full)), 1):
        block = m.group(2)
        label = re.search(r"\\label\{([^}]*)\}", block)
        where = f"Table {n}" + (f" ({label.group(1)})" if label else "")
        cap = reader.plain(caption(block))
        for tab in sota.tabulars(block):
            rows = [sota.RULES.sub("", r) for r in sota.ROW_END.split(tab)]
            rows = [r for r in rows if any(reader.plain(c) for c, _ in cells_with_spans(r))]
            header = []
            for r in rows:
                cells = [c for c, _ in cells_with_spans(r)]
                if any(sota.cell_number(c) for c in cells[1:]):
                    break
                header.append(cells)
            for r in rows[len(header):]:
                cells = cells_with_spans(r)
                if reader.CITE.search(r):
                    skipped["cited"] += sum(bool(sota.cell_number(c)) for c, _ in cells)
                    continue
                name = reader.plain(cells[0][0]) if cells else ""
                for j, (c, offset) in enumerate(cells):
                    value = sota.cell_number(c)
                    if not value or j == 0:
                        continue
                    heads = [reader.plain(h[j]) for h in header if j < len(h) and reader.plain(h[j])]
                    column = " / ".join(heads) or f"column {j + 1}"
                    pos = r.find(value, offset)
                    hints = f"{column} {cap}"
                    out.append({"kind": "table", "value": value, "percent": "\\%" in c,
                                "location": f"{where}, row {name}, column {column}", "column": column,
                                "method": name, "context": f"{cap} | {column} | {name} = {reader.plain(c)}",
                                "split": split_hint(column) or split_hint(cap), "metric": metric_hint(hints),
                                "raw": r, "start": pos, "end": pos + len(value), "sentence_key": None})
    return out


def near_cite(cites: list[tuple[int, int]], start: int, end: int) -> bool:
    return any(s - NEAR_CITE <= end and start <= e + NEAR_CITE for s, e in cites)


def setting(masked: str, start: int, end: int) -> bool:
    before = re.sub(r"\\[a-z]+|[{}~$]", " ", masked[max(0, start - 40):start])
    return bool(SETTING_BEFORE.search(before) or SETTING_AFTER.match(masked[end:end + 20]))


def sentence_items(full: str, skipped: dict) -> tuple[list[dict], list[dict]]:
    numbers, derived, count = [], [], {}
    for section, para in sectioned_paragraphs(full):
        count[section] = count.get(section, 0) + 1
        where = f"§{section} ¶{count[section]}"
        for k, sent in enumerate(sentences(para)):
            key = f"{where} s{k + 1}"
            cites = [(c.start(), c.end()) for c in reader.CITE.finditer(sent)]
            masked = reader.CITE.sub(lambda c: " " * len(c.group(0)), sent)
            masked = reader.REFS.sub(lambda c: " " * len(c.group(0)), masked)
            plain = reader.plain(sent)
            claims, taken = [], []
            for kind, rx in DERIVED:
                for m in rx.finditer(masked):
                    if near_cite(cites, m.start(), m.end()) or any(m.start() < e and s < m.end() for s, e in taken):
                        continue
                    claims.append(derived_item(kind, m, masked))
                    if kind != "from_to":
                        taken.append((m.start(1) if kind != "by" else m.start(2),
                                       m.end(1) if kind != "by" else m.end(2)))
            if not claims:
                m = OUTPERFORMS.search(masked)
                if m and not near_cite(cites, m.start(), m.end()):
                    claims.append({"kind": "outperforms", "value": "", "word": m.group(1).split()[0].lower(),
                                   "unit": "", "start": None, "end": None, "operand_starts": []})
            for c in claims:
                derived.append(dict(c, location=where, context=plain, raw=sent, sentence_key=key,
                                    metric=metric_hint(masked), split=split_hint(masked)))
            for num in sota.SENTENCE_NUMBER.finditer(masked):
                s, e = num.start(1), num.end(1)
                if any(s >= a and e <= b for a, b in taken):
                    continue
                if near_cite(cites, s, e):
                    skipped["cited"] += 1
                    continue
                if setting(masked, s, e):
                    skipped["setting"] += 1
                    continue
                numbers.append({"kind": "sentence", "value": num.group(1), "percent": bool(num.group(2)),
                                "location": where, "column": "", "method": "", "context": plain,
                                "split": split_hint(masked, s), "metric": metric_hint(masked, s),
                                "raw": sent, "start": s, "end": e, "sentence_key": key})
    return numbers, derived


def derived_item(kind: str, m, masked: str) -> dict:
    if kind == "from_to":
        word = masked[max(0, m.start() - 60):m.start()].split()
        verb = next((w for w in reversed(word) if LESS.match(w) or MORE.match(w) or BETTER.match(w)), "")
        return {"kind": "from_to", "value": "", "word": verb.lower(), "unit": "", "start": None, "end": None,
                "from": m.group(1), "to": m.group(3), "operand_starts": [m.start(1), m.start(3)]}
    if kind == "by":
        unit = (m.group(3) or "").replace("\\", "")
        return {"kind": "relative" if unit == "%" else "absolute", "value": m.group(2), "word": m.group(1).lower(),
                "unit": unit or "points", "start": m.start(2), "end": m.end(2), "operand_starts": []}
    if kind == "points":
        return {"kind": "absolute", "value": m.group(1), "word": m.group(2).lower(), "unit": "points",
                "start": m.start(1), "end": m.end(1), "operand_starts": []}
    if kind == "ratio":
        return {"kind": "ratio", "value": m.group(1), "word": (m.group(2) or "").lower(), "unit": "x",
                "start": m.start(1), "end": m.end(1), "operand_starts": []}
    return {"kind": "relative", "value": m.group(1), "word": m.group(2).lower(), "unit": "%",
            "start": m.start(1), "end": m.end(1), "operand_starts": []}


def relation(word: str) -> str:
    """What the claim says of the subject against the reference: less, more, better, worse or ''."""
    for name, rx in (("less", LESS), ("more", MORE), ("better", BETTER), ("worse", WORSE)):
        if rx.match(word or ""):
            return name
    if (word or "").startswith("faster"):
        return "faster"
    if (word or "").startswith("slower"):
        return "slower"
    return ""


def flexible(text: str) -> str:
    return r"\s+".join(re.escape(t) for t in text.split())


def locate(files: dict[str, str], raw: str, start: int, end: int) -> dict | None:
    """The file and the shortest span around raw[start:end], grown a word at a time on
    each side, that occurs exactly once in exactly one file (whitespace may differ, as
    LaTeX reads it); with the offset of the number inside that span and its line."""
    if start is None or start < 0:
        return None
    left, right = start, end
    while True:
        hits = [(f, m) for f, text in files.items() for m in re.finditer(flexible(raw[left:right]), text)]
        if not hits:
            return None
        if len(hits) == 1 and (right - left >= 30 or (left == 0 and right == len(raw))):
            f, m = hits[0]
            quote = m.group(0)
            offset = locate_number(quote, raw[left:start], raw[start:end])
            if offset is None:
                return None
            return {"file": f, "quote": quote, "at": offset, "length": end - start, "pos": m.start(),
                    "line": files[f].count("\n", 0, m.start() + offset) + 1}
        if left == 0 and right == len(raw):
            return None
        if left > 0:
            i = raw.rfind(" ", 0, max(left - 1, 0))
            left = i + 1 if i >= 0 else 0
        if right < len(raw):
            i = raw.find(" ", right + 1)
            right = i if i >= 0 else len(raw)


def locate_number(quote: str, before: str, value: str) -> int | None:
    """The offset of value in quote after the words of before."""
    words = before.split()
    pos = 0
    for w in words:
        i = quote.find(w, pos)
        if i < 0:
            return None
        pos = i + len(w)
    i = quote.find(value, pos)
    return i if i >= 0 else None

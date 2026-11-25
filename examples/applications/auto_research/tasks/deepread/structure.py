"""Turn a work's text into what deep_read reads: sections, paragraphs (with inline
citation markers), numbered display equations and the reference list with how often
each is cited. Plain code, no model calls.

LaTeX: the flattened source's own macros (no-argument and one-argument \\newcommand,
\\renewcommand, \\providecommand, \\def) are expanded in prose; \\cite commands become
markers "[cite: key1, key2]"; references to equation, section, figure and table labels
become their numbers, counted as LaTeX counts them (align rows one number each unless
\\nonumber or \\notag, \\tag kept, starred environments unnumbered, appendix sections
lettered). Floats give their captions as paragraphs; their bodies are left out.
PDF (pdftotext): headings are short numbered lines, paragraphs are blocks between blank
lines, references follow the References heading, and numeric citations "[3]", "[3, 5]",
"[3-5]" are counted. A PDF yields no equations.
"""

import re

from auto_research.tasks.citations.refs import ARXIV_ID, DOI, parse_bib
from auto_research.tasks.reading.reader import plain

MARK = re.compile(r"\[cite: ([^\]]*)\]")
CITE = re.compile(r"\\[a-zA-Z]*cite[a-zA-Z]*\*?(?:\s*\[[^\]]*\]){0,2}\s*\{([^}]*)\}")
REFCMD = re.compile(r"\\(eqref|ref|autoref|cref|Cref|pageref)\*?\s*\{([^}]*)\}")
HEADING = re.compile(r"\\(part|chapter|section|subsection|subsubsection)(\*?)\s*(?:\[[^\]]*\])?\s*(?=\{)")
DISPLAY_ENVS = ("equation", "align", "gather", "multline", "eqnarray", "flalign", "alignat", "displaymath")
FLOAT_ENVS = ("figure", "table", "wrapfigure", "wraptable", "algorithm", "SCfigure", "sidewaystable", "sidewaysfigure")
TOKEN = re.compile(
    r"\\(?:part|chapter|section|subsection|subsubsection)\*?\s*(?:\[[^\]]*\])?\s*(?=\{)"
    r"|\\appendix\b"
    r"|\\begin\{(?:" + "|".join(DISPLAY_ENVS + FLOAT_ENVS) + r")\*?\}"
    r"|\\begin\{abstract\}|\\end\{abstract\}"
    r"|\\\[|\$\$"
    r"|\\paragraph\*?\s*(?=\{)")
MIN_WORDS = 3
LEVEL = {"part": 0, "chapter": 0, "section": 1, "subsection": 2, "subsubsection": 3}


def group(text: str, i: int) -> tuple[str, int]:
    """The brace group starting at text[i] == "{": (its content, the index after it)."""
    if i >= len(text) or text[i] != "{":
        return "", i
    depth, j = 0, i
    while j < len(text):
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[i + 1:j], j + 1
        j += 1
    return text[i + 1:], len(text)


# Macros

DEF = re.compile(r"\\(?:newcommand|renewcommand|providecommand)\*?\s*(\{\\[A-Za-z@]+\}|\\[A-Za-z@]+)\s*(?:\[(\d)\])?"
                 r"\s*(\[[^\]]*\])?\s*(?=\{)|\\def\s*(\\[A-Za-z@]+)\s*(#1)?\s*(?=\{)")


def macros(tex: str) -> dict[str, tuple[int, str]]:
    """The no- and one-argument macros the source defines: name -> (arguments, body)."""
    found = {}
    for m in DEF.finditer(tex):
        name = (m.group(1) or m.group(4) or "").strip("{}")
        args = int(m.group(2) or 0) if m.group(1) else (1 if m.group(5) else 0)
        body, _ = group(tex, m.end())
        if name and args <= 1 and len(body) < 400:
            found[name[1:]] = (args, body)
    return found


def expand(text: str, defs: dict[str, tuple[int, str]]) -> str:
    if not defs:
        return text
    zero = sorted((n for n, (a, _) in defs.items() if a == 0), key=len, reverse=True)
    one = sorted((n for n, (a, _) in defs.items() if a == 1), key=len, reverse=True)
    z = re.compile(r"\\(" + "|".join(map(re.escape, zero)) + r")(?![A-Za-z@])(?:\{\})?") if zero else None
    o = re.compile(r"\\(" + "|".join(map(re.escape, one)) + r")(?![A-Za-z@])\s*\{([^{}]*)\}") if one else None
    for _ in range(4):
        before = text
        if z:
            text = z.sub(lambda m: defs[m.group(1)][1], text)
        if o:
            text = o.sub(lambda m: defs[m.group(1)][1].replace("#1", m.group(2)), text)
        if text == before:
            break
    return text


# LaTeX

def body_of(tex: str) -> str:
    start = tex.find("\\begin{document}")
    end = tex.find("\\end{document}")
    text = tex[start + len("\\begin{document}") if start >= 0 else 0:end if end >= 0 else len(tex)]
    return re.sub(r"\\(?:bibliography|bibliographystyle)\s*\{[^}]*\}|\\begin\{thebibliography\}.*?\\end\{thebibliography\}",
                  "", text, flags=re.S)


def split_rows(content: str) -> list[str]:
    """The rows of an align-like environment: split at top-level \\\\ outside nested environments."""
    rows, depth, env, start, i = [], 0, 0, 0, 0
    while i < len(content):
        if content.startswith("\\begin{", i):
            env += 1
        elif content.startswith("\\end{", i):
            env -= 1
        c = content[i]
        if c == "\\" and content.startswith("\\\\", i) and depth == 0 and env == 0:
            rows.append(content[start:i])
            i += 2
            m = re.match(r"\s*\[[^\]]*\]", content[i:])
            if m:
                i += m.end()
            start = i
            continue
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    rows.append(content[start:])
    return [r for r in rows if r.strip()]


class Latex:
    """One pass over the body: sections, paragraphs, displays and labels in order."""

    def __init__(self, tex: str):
        self.defs = macros(tex)
        self.sections, self.items, self.labels = [], [], {}
        self.counters = {"section": 0, "subsection": 0, "subsubsection": 0, "figure": 0, "table": 0, "eq": 0}
        self.appendix = False
        self.current = None
        title = re.search(r"\\title\s*(?:\[[^\]]*\])?\s*(?=\{)", tex)
        self.title = plain(expand(group(tex, title.end())[0], self.defs)) if title else ""
        self.scan(body_of(tex))

    def section(self, title: str, level: int, number: str, label: str = "") -> None:
        sid = f"S{len(self.sections) + 1}"
        self.current = {"id": sid, "title": title, "level": level, "number": number, "label": label}
        self.sections.append(self.current)

    def text(self, chunk: str) -> None:
        if self.current is None:
            return
        for part in re.split(r"\n\s*\n", chunk):
            if part.strip():
                self.items.append({"kind": "text", "section": self.current["id"], "raw": part.strip()})

    def scan(self, body: str) -> None:
        i = 0
        while True:
            m = TOKEN.search(body, i)
            if not m:
                self.text(body[i:])
                return
            self.text(body[i:m.start()])
            tok = m.group(0)
            i = m.end()
            if tok.startswith("\\appendix"):
                self.appendix = True
                self.counters["section"] = 0
            elif tok.startswith("\\begin{abstract}"):
                end = body.find("\\end{abstract}", i)
                end = end if end >= 0 else len(body)
                self.section("Abstract", 1, "")
                self.text(body[i:end])
                i = end + len("\\end{abstract}")
            elif tok.startswith("\\end{abstract}"):
                pass
            elif tok.startswith("\\paragraph"):
                head, i = group(body, i)
                rest = body.find("\n\n", i)
                rest = rest if rest >= 0 else len(body)
                nxt = TOKEN.search(body, i)
                stop = min(rest, nxt.start() if nxt else len(body))
                head = head.strip()
                self.text(f"\\textbf{{{head}{'' if head[-1:] in '.:?!' else '.'}}} " + body[i:stop])
                i = stop
            elif HEADING.match(body, m.start()):
                h = HEADING.match(body, m.start())
                title, i = group(body, i)
                label = re.match(r"\s*\\label\{([^}]*)\}", body[i:])
                if label:
                    i += label.end()
                self.heading(h.group(1), bool(h.group(2)), title, label.group(1) if label else "")
            elif tok in ("\\[", "$$"):
                close = "\\]" if tok == "\\[" else "$$"
                end = body.find(close, i)
                end = end if end >= 0 else len(body)
                self.display("displaymath", True, body[i:end])
                i = end + len(close)
            else:
                env = re.match(r"\\begin\{([A-Za-z]+)(\*?)\}", tok)
                name, star = env.group(1), env.group(2)
                close = f"\\end{{{name}{star}}}"
                end = body.find(close, i)
                end = end if end >= 0 else len(body)
                if name in DISPLAY_ENVS:
                    self.display(name, bool(star) or name == "displaymath", body[i:end])
                else:
                    self.float(name, body[i:end])
                i = end + len(close)

    def heading(self, kind: str, star: bool, title: str, label: str) -> None:
        level = LEVEL[kind]
        number = ""
        if not star and kind in self.counters:
            self.counters[kind] += 1
            for deeper in {"section": ("subsection", "subsubsection"), "subsection": ("subsubsection",)}.get(kind, ()):
                self.counters[deeper] = 0
            top = self.counters["section"]
            parts = [chr(64 + top) if self.appendix and top <= 26 else str(top)]
            if level >= 2:
                parts.append(str(self.counters["subsection"]))
            if level >= 3:
                parts.append(str(self.counters["subsubsection"]))
            number = ".".join(parts)
        if label:
            self.labels[label] = number
        self.section(plain(expand(title, self.defs)) or kind, level, number, label)

    def display(self, env: str, unnumbered: bool, content: str) -> None:
        if self.current is None:
            self.section("Front matter", 1, "")
        multi = env in ("align", "gather", "eqnarray", "flalign", "alignat")
        rows = split_rows(content) if multi else [content]
        if env == "alignat" and rows:
            rows[0] = re.sub(r"^\s*\{\d+\}", "", rows[0])
        out = []
        for row in rows:
            label = re.search(r"\\label\{([^}]*)\}", row)
            tag = re.search(r"\\tag\*?\{([^}]*)\}", row)
            number = ""
            if tag:
                number = tag.group(1).strip()
            elif not unnumbered and not re.search(r"\\(?:nonumber|notag)\b", row):
                self.counters["eq"] += 1
                number = str(self.counters["eq"])
            if label and number:
                self.labels[label.group(1)] = number
            clean = re.sub(r"\\(?:label|tag\*?)\{[^}]*\}|\\(?:nonumber|notag)\b", "", row).strip()
            out.append({"number": number, "label": label.group(1) if label else "", "latex": clean})
        self.items.append({"kind": "display", "section": self.current["id"], "env": env, "rows": out,
                           "latex": content.strip()})

    def float(self, env: str, content: str) -> None:
        kind = "table" if "table" in env else "figure" if "figure" in env else ""
        cap = re.search(r"\\caption\s*(?:\[[^\]]*\])?\s*(?=\{)", content)
        if not cap or not kind or self.current is None:
            return
        self.counters[kind] += 1
        number = str(self.counters[kind])
        for label in re.findall(r"\\label\{([^}]*)\}", content):
            self.labels[label] = number
        caption, _ = group(content, cap.end())
        name = "Figure" if kind == "figure" else "Table"
        self.items.append({"kind": "caption", "section": self.current["id"], "raw": f"{name} {number}: {caption}"})


SYMBOLS = {"in": "∈", "times": "×", "cdot": "·", "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥", "approx": "≈",
           "sim": "~", "neq": "≠", "ne": "≠", "to": "→", "rightarrow": "→", "infty": "∞", "sum": "∑", "pm": "±",
           "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "lambda": "λ", "mu": "μ",
           "sigma": "σ", "theta": "θ", "pi": "π", "tau": "τ", "ell": "ℓ", "mathbb{R}": "ℝ", "ldots": "...",
           "cdots": "...", "dots": "..."}
SYMBOL = re.compile(r"\\(" + "|".join(re.escape(k) for k in sorted(SYMBOLS, key=len, reverse=True)) + r")(?![A-Za-z])")


def readable(raw: str, defs: dict, labels: dict) -> str:
    """LaTeX prose as a reader sees it, with citation markers kept."""
    s = expand(raw, defs)
    s = re.sub(r"\\verb\*?(.)(.*?)\1", r"\2", s)
    s = CITE.sub(lambda m: "[cite: " + ", ".join(k.strip() for k in m.group(1).split(",") if k.strip()) + "]", s)
    s = REFCMD.sub(lambda m: ref_text(m.group(1), m.group(2), labels), s)
    s = re.sub(r"\\(?:begin|end)\{[^}]*\}(?:\{[^}]*\})?|\\item\b(?:\[[^\]]*\])?", lambda m: " • " if m.group(0).startswith("\\item") else " ", s)
    s = re.sub(r"\\label\{[^}]*\}|\\(?:vspace|hspace)\*?\{[^}]*\}", " ", s)
    s = s.replace("\\\\", " ").replace("``", '"').replace("''", '"')
    s = SYMBOL.sub(lambda m: " " + SYMBOLS[m.group(1)] + " ", s)
    s = plain(s)
    return " ".join(s.split())


def ref_text(cmd: str, labels_arg: str, labels: dict) -> str:
    out = []
    for label in (x.strip() for x in labels_arg.split(",")):
        n = labels.get(label)
        if n is None or n == "":
            out.append("?")
        else:
            out.append(f"({n})" if cmd == "eqref" else n)
    return ", ".join(out)


def bbl_entries(text: str) -> dict[str, dict]:
    """The \\bibitem entries of a .bbl: key -> {title, authors, venue, text, arxiv, doi}."""
    out = {}
    pat = re.compile(r"\\bibitem\s*(?:\[(?:[^\[\]]|\[[^\]]*\])*\])?\s*\{([^}]+)\}")
    marks = list(pat.finditer(text))
    for k, m in enumerate(marks):
        end = marks[k + 1].start() if k + 1 < len(marks) else len(text)
        chunk = text[m.end():end].split("\\end{thebibliography}")[0]
        parts = [plain(p) for p in re.split(r"\\newblock", chunk)]
        joined = " ".join(" ".join(parts).split())
        arxiv, doi = ARXIV_ID.search(chunk), DOI.search(chunk)
        out[m.group(1).strip()] = {"authors": parts[0] if parts else "", "title": parts[1].strip(" .") if len(parts) > 1 else "",
                                   "venue": " ".join(parts[2:]).strip(), "text": joined,
                                   "arxiv": arxiv.group(1) if arxiv else "", "doi": doi.group(1).lower() if doi else ""}
    return out


def year_of(text: str) -> str:
    m = re.search(r"\b(?:19|20)\d{2}\b", text or "")
    return m.group(0) if m else ""


def latex_document(work: dict) -> dict:
    """The document of a LaTeX work (see the module docstring)."""
    tex = work["tex"]
    parsed = Latex(tex)
    defs, labels = parsed.defs, parsed.labels
    paragraphs, displays, order, counts, first = [], [], [], {}, []
    for item in parsed.items:
        if item["kind"] == "display":
            did = f"D{len(displays) + 1}"
            displays.append({"id": did, "section": item["section"], "env": item["env"], "rows": item["rows"],
                             "latex": item["latex"], "before": paragraphs[-1]["id"] if paragraphs else ""})
            order.append(did)
            continue
        text = readable(item["raw"], defs, labels)
        if len(text.split()) < MIN_WORDS:
            continue
        cites = [k.strip() for g in MARK.findall(text) for k in g.split(",") if k.strip()]
        for k in cites:
            counts[k] = counts.get(k, 0) + 1
            if k not in first:
                first.append(k)
        pid = f"P{len(paragraphs) + 1}"
        paragraphs.append({"id": pid, "section": item["section"], "kind": item["kind"], "text": text, "cites": cites})
        order.append(pid)
    bib = {}
    for name, text in work["files"].items():
        if name.endswith(".bib"):
            for ref in parse_bib(text):
                bib.setdefault(ref["key"], ref)
    bbl = {}
    for name, text in work["files"].items():
        if name.endswith(".bbl"):
            bbl.update(bbl_entries(text))
    refs = []
    for n, key in enumerate(first, 1):
        b, t = bib.get(key) or {}, bbl.get(key) or {}
        refs.append({"key": key, "n": n, "count": counts[key],
                     "title": plain(b.get("title") or "") or t.get("title", ""),
                     "authors": ", ".join(b.get("authors") or []) or t.get("authors", ""),
                     "year": b.get("year") or year_of(t.get("text", "")),
                     "venue": plain(b.get("venue") or "") or t.get("venue", ""),
                     "arxiv": b.get("arxiv") or t.get("arxiv", ""), "doi": (b.get("doi") or t.get("doi", "")).lower(),
                     "text": t.get("text", "")})
    sections = [s for s in parsed.sections if any(p["section"] == s["id"] for p in paragraphs)
                or any(d["section"] == s["id"] for d in displays)]
    return {"title": parsed.title, "sections": sections, "paragraphs": paragraphs, "displays": displays,
            "order": order, "references": refs, "source": "latex"}


# PDF

NUMBERED = re.compile(r"^(?:\d+(?:\.\d+){0,3}\.?|[A-Z](?:\.\d+){0,3}\.?)\s+[A-Z][^.]{1,80}$")
NAMED = re.compile(r"^(?:abstract|introduction|related work|background|method|methods|experiments|results|discussion|"
                   r"conclusions?|limitations|acknowledge?ments?|references|bibliography|appendix)$", re.I)
REFS_HEAD = re.compile(r"^(?:\d+\.?\s+)?(?:references|bibliography)$", re.I)
NUM_CITE = re.compile(r"\[(\d{1,3}(?:\s*[,\u2013\-]\s*\d{1,3})*)\]")


def cite_numbers(group_text: str) -> list[int]:
    out = []
    for part in re.split(r"\s*,\s*", group_text):
        bounds = re.split(r"\s*[\u2013\-]\s*", part)
        if len(bounds) == 2 and all(b.isdigit() for b in bounds) and 0 < int(bounds[1]) - int(bounds[0]) < 30:
            out += list(range(int(bounds[0]), int(bounds[1]) + 1))
        elif part.strip().isdigit():
            out.append(int(part))
    return out


RUNNING_HEAD = re.compile(r"^(?:published as|under review|preprint|accepted (?:at|to|as)|workshop|arxiv:\s*\d)", re.I)
SMALL_CAPS = re.compile(r"\b([A-Z]) (?=[A-Z]{2,}\b)")


def small_caps(text: str) -> str:
    """Undo pdftotext's split of small-caps words in a heading ("R EFERENCES", "A M ETHOD"
    becomes "REFERENCES", "A METHOD"): one pass, so a real one-letter word stays apart
    unless the next word itself was split."""
    return SMALL_CAPS.sub(r"\1", text) if sum(c.isupper() for c in text) > sum(c.islower() for c in text) else text


def pdf_blocks(text: str) -> list[str]:
    blocks = []
    for page in text.split("\f"):
        for block in re.split(r"\n\s*\n", page):
            lines = [ln.strip() for ln in block.splitlines() if ln.strip() and not re.fullmatch(r"\d{1,4}", ln.strip())]
            if not lines:
                continue
            joined = ""
            for ln in lines:
                if joined.endswith("-") and ln[:1].islower():
                    joined = joined[:-1] + ln
                else:
                    joined = (joined + " " + ln).strip()
            blocks.append(joined)
    return blocks


def pdf_document(work: dict) -> dict:
    """The document of a PDF work (see the module docstring)."""
    blocks = pdf_blocks(work["text"])
    sections, paragraphs, order, refs_text = [], [], [], []
    current, in_refs = None, False
    blocks = [small_caps(b) if len(b) <= 160 else b for b in blocks]
    heads = [b for b in blocks[:6] if not RUNNING_HEAD.match(b)]
    title = heads[0] if heads and len(heads[0].split()) <= 25 else ""
    for b in blocks:
        short = len(b) <= 90 and len(b.split()) <= 12
        if short and REFS_HEAD.match(b):
            in_refs = True
            continue
        if in_refs:
            if short and (NUMBERED.match(b) and b.split()[0][:1].isalpha() or b.lower().startswith("appendix")):
                in_refs = False
            else:
                refs_text.append(b)
                continue
        if short and (NUMBERED.match(b) or NAMED.match(b)):
            current = {"id": f"S{len(sections) + 1}", "title": b, "level": 1 + b.split()[0].count("."), "number": "",
                       "label": ""}
            sections.append(current)
            continue
        if current is None or len(b.split()) < MIN_WORDS:
            continue
        pid = f"P{len(paragraphs) + 1}"
        paragraphs.append({"id": pid, "section": current["id"], "kind": "text", "text": b, "cites": []})
        order.append(pid)
    refs = pdf_references(refs_text)
    by_n = {r["n"]: r for r in refs}
    for p in paragraphs:
        for g in NUM_CITE.findall(p["text"]):
            for n in cite_numbers(g):
                if n in by_n:
                    by_n[n]["count"] += 1
                    p["cites"].append(by_n[n]["key"])
    sections = [s for s in sections if any(p["section"] == s["id"] for p in paragraphs)]
    return {"title": title, "sections": sections, "paragraphs": paragraphs, "displays": [], "order": order,
            "references": refs, "source": "pdf"}


def pdf_references(blocks: list[str]) -> list[dict]:
    text = "\n".join(blocks)
    numbered = re.split(r"(?:^|\s)\[(\d{1,3})\]\s+", text)
    entries = []
    if len(numbered) > 3:
        for k in range(1, len(numbered) - 1, 2):
            entries.append((int(numbered[k]), " ".join(numbered[k + 1].split())))
    else:
        entries = [(i, " ".join(b.split())) for i, b in enumerate(blocks, 1)]
    out = []
    for n, entry in entries:
        parts = [p.strip() for p in re.split(r"\.\s+", entry) if p.strip()]
        guess = next((p for p in parts[1:] if len(p.split()) >= 3), parts[0] if parts else "")
        arxiv, doi = ARXIV_ID.search(entry), DOI.search(entry)
        year = re.search(r"\b(19|20)\d{2}\b", entry)
        out.append({"key": f"R{n}", "n": n, "count": 0, "title": guess.strip(" ."), "authors": parts[0] if parts else "",
                    "year": year.group(0) if year else "", "venue": "", "arxiv": arxiv.group(1) if arxiv else "",
                    "doi": doi.group(1).lower() if doi else "", "text": entry})
    return out


def document(work: dict) -> dict:
    return latex_document(work) if work["kind"] == "latex" else pdf_document(work)


def strip_marks(text: str) -> str:
    return " ".join(MARK.sub(" ", text).split())


__all__ = ["MARK", "document", "expand", "latex_document", "macros", "pdf_document", "readable", "strip_marks"]

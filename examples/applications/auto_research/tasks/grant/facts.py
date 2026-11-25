"""The funders grant_proposal writes for, read from funders.json, and how a section is
measured against them: its length in the funder's unit and the elements it must hold.
Plain code.

The limits follow each funder's guide at the time of writing; the report says to check
them against the current call.
"""

import json
import re
from pathlib import Path

FILE = Path(__file__).with_name("funders.json")
UNITS = ("chars", "words", "zh_chars", "pages")
WORDS_PER_PAGE = 500            # English, single-spaced 11 pt
ZH_CHARS_PER_PAGE = 800         # 小四号宋体
LEVELS = ("error", "note")
CJK = r"　-〿㐀-䶿一-鿿豈-﫿＀-￯"
ZH_UNIT = re.compile(rf"[{CJK}]|[A-Za-z0-9][A-Za-z0-9.\-_%]*")


def load() -> dict[str, dict]:
    """Every funder by key, with extends resolved; the _doc entry left out."""
    raw = json.loads(FILE.read_text(encoding="utf-8"))
    out = {}
    for key, entry in raw.items():
        if key.startswith("_"):
            continue
        base = raw[entry["extends"]] if entry.get("extends") else {}
        out[key] = {**base, **{k: v for k, v in entry.items() if k != "extends"}, "key": key}
    return out


def funder(name: str) -> dict:
    """The funder a name or alias means; ValueError lists the known ones."""
    wanted = str(name or "").strip().lower()
    funders = load()
    for key, f in funders.items():
        if wanted == key or wanted in [a.lower() for a in f.get("aliases", [])]:
            return f
    raise ValueError(f"unknown funder {name!r}; use one of {', '.join(funders)}")


def problems(f: dict) -> list[str]:
    """What keeps a funder entry from being usable (the tests run this on every entry)."""
    found = []
    for k in ("name", "language", "duration_months", "aims", "sections", "budget"):
        if k not in f:
            found.append(f"{f['key']}: missing {k}")
    if f.get("language") not in ("en", "zh"):
        found.append(f"{f['key']}: language must be en or zh")
    aims = f.get("aims") or {}
    if not (2 <= aims.get("min", 0) <= aims.get("max", 0)) or "{n}" not in aims.get("label", ""):
        found.append(f"{f['key']}: aims needs min <= max and a label with {{n}}")
    ids = [s.get("id") for s in f.get("sections", [])]
    if len(set(ids)) != len(ids) or not all(ids):
        found.append(f"{f['key']}: section ids must be unique")
    for s in f.get("sections", []):
        limit = s.get("limit")
        if limit is not None and (limit.get("unit") not in UNITS or not limit.get("value")):
            found.append(f"{f['key']}.{s['id']}: limit unit must be one of {', '.join(UNITS)}")
        for r in s.get("require", []):
            if r.get("level") not in LEVELS or not (r.get("heading") or r.get("text")):
                found.append(f"{f['key']}.{s['id']}: require item {r.get('what')} needs a level and a pattern")
        if s.get("aims_named", "error") not in LEVELS:
            found.append(f"{f['key']}.{s['id']}: aims_named must be error or note")
    return found


def zh_chars(text: str) -> int:
    """Characters as a Chinese form counts them: each CJK character or full-width
    punctuation mark is 1, and so is each run of Latin letters or digits."""
    return len(ZH_UNIT.findall(text))


def words(text: str) -> int:
    return len(text.split())


def measure(text: str, unit: str, language: str) -> float:
    """The text's length in unit; pages are estimated from words (English) or characters (Chinese)."""
    if unit == "chars":
        return len(text)
    if unit == "words":
        return words(text)
    if unit == "zh_chars":
        return zh_chars(text)
    if unit == "pages":
        return round(zh_chars(text) / ZH_CHARS_PER_PAGE if language == "zh" else words(text) / WORDS_PER_PAGE, 2)
    raise ValueError(f"unknown unit {unit}")


def target(limit: dict | None, language: str) -> str:
    """The length the writer aims for, in words or characters it can count."""
    if not limit:
        return "no fixed limit; as long as the content needs, without padding"
    value, unit = limit["value"], limit["unit"]
    if unit == "pages":
        per = ZH_CHARS_PER_PAGE if language == "zh" else WORDS_PER_PAGE
        what = "Chinese characters" if language == "zh" else "words"
        return f"at most {value} pages, about {int(value * per * 0.9)} {what}"
    names = {"chars": "characters including spaces", "words": "words", "zh_chars": "Chinese characters (字)"}
    return f"at most {value} {names[unit]}; aim for about 90% of it"


def unit_name(unit: str) -> str:
    return {"chars": "字符", "words": "词", "zh_chars": "字", "pages": "页"}[unit]


def aim_label(f: dict, n: int) -> str:
    return f["aims"]["label"].format(n=n)


def aim_pattern(f: dict, n: int) -> re.Pattern:
    """The aim's label with any spacing, not followed by another digit ("Aim 1" is not "Aim 12")."""
    before, after = f["aims"]["label"].split("{n}")
    return re.compile(re.escape(before.strip()) + r"\s*" + str(n) + r"(?!\d)" + re.escape(after.strip()), re.I)


def check(f: dict, section: dict, text: str, headings: list[str], aims: int) -> list[dict]:
    """The section's findings: over the length limit, a required element or aim label missing.
    Each is {level, what, problem}."""
    found = []
    limit = section.get("limit")
    if limit:
        length = measure(text, limit["unit"], f["language"])
        if length > limit["value"]:
            found.append({"level": "error", "what": "length",
                          "problem": f"{length} {unit_name(limit['unit'])}，超过上限 {limit['value']}"})
    for r in section.get("require", []):
        if r.get("heading"):
            ok = any(re.search(r["heading"], h.strip()) for h in headings)
        else:
            ok = bool(re.search(r["text"], "\n".join(headings) + "\n" + text))
        if not ok:
            found.append({"level": r["level"], "what": r["what"], "problem": f"缺少：{r['what']}"})
    level = section.get("aims_named")
    if level:
        whole = "\n".join(headings) + "\n" + text
        missing = [aim_label(f, n) for n in range(1, aims + 1) if not aim_pattern(f, n).search(whole)]
        if missing:
            found.append({"level": level, "what": "aims", "problem": f"没有写到：{'、'.join(missing)}"})
    return found


__all__ = ["check", "funder", "load", "measure", "problems", "target", "zh_chars"]

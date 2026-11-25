"""Licenses: SPDX identifiers and license texts found in a file, and whether a file under
one license may sit in a repository released under another. A small built-in matcher on
the distinctive sentences of each license (as REUSE and ScanCode key on them);
scancode-toolkit is used instead when it is importable."""

import re

SPDX_LINE = re.compile(r"SPDX-License-Identifier:\s*([A-Za-z0-9.+\-() ]+?)\s*(?:\*/|-->|$)", re.M)
# (SPDX id, sentences that must all appear in the normalised text). The first match wins,
# so more specific licenses come first.
RULES = [
    ("MPL-2.0", ["mozilla public license", "2.0"]),      # its text names the GPL family as secondary licenses
    ("AGPL-3.0", ["gnu affero general public license"]),
    ("LGPL-3.0", ["gnu lesser general public license", "version 3"]),
    ("LGPL-2.1", ["gnu lesser general public license", "version 2.1"]),
    ("LGPL-2.1", ["gnu lesser general public license"]),
    ("LGPL-2.0", ["gnu library general public license"]),
    ("GPL-3.0", ["gnu general public license", "version 3"]),
    ("GPL-2.0", ["gnu general public license", "version 2"]),
    ("GPL-3.0", ["gnu general public license"]),
    ("Apache-2.0", ["apache license", "version 2.0"]),
    ("Apache-2.0", ["apache-2.0"]),
    ("CC-BY-NC-4.0", ["attribution-noncommercial"]),
    ("CC-BY-NC-4.0", ["attribution noncommercial"]),
    ("CC-BY-SA-4.0", ["attribution-sharealike"]),
    ("CC0-1.0", ["cc0 1.0"]),
    ("CC-BY-4.0", ["creative commons attribution"]),
    ("BSD-3-Clause", ["redistribution and use in source and binary forms", "neither the name"]),
    ("BSD-3-Clause", ["redistribution and use in source and binary forms", "names of its contributors"]),
    ("BSD-2-Clause", ["redistribution and use in source and binary forms"]),
    ("MIT", ["permission is hereby granted, free of charge, to any person obtaining a copy"]),
    ("MIT", ["mit license"]),
    ("ISC", ["permission to use, copy, modify, and/or distribute this software for any purpose"]),
    ("Unlicense", ["this is free and unencumbered software released into the public domain"]),
    ("BSL-1.0", ["boost software license"]),
    ("Zlib", ["altered source versions must be plainly marked as such"]),
    ("LPPL-1.3c", ["latex project public license"]),
    ("Python-2.0", ["python software foundation license"]),
]
FULL_TEXT = ("terms and conditions", "end of terms and conditions")
LATER = "any later version"
TITLE_LINES = 8

PERMISSIVE = {"MIT", "BSD-2-Clause", "BSD-3-Clause", "ISC", "Unlicense", "0BSD", "Zlib", "BSL-1.0", "CC0-1.0",
              "Python-2.0", "X11", "PSF-2.0"}
GPL_FAMILY = {"GPL-2.0", "GPL-3.0", "AGPL-3.0", "LGPL-2.0", "LGPL-2.1", "LGPL-3.0"}


def normalise(text: str) -> str:
    lines = [re.sub(r"^\s*(?:#+|//+|/?\*+/?|--|%+|;+|<!--|-->|\"\"\"|''')", " ", line) for line in text.splitlines()]
    return re.sub(r"\s+", " ", " ".join(lines)).lower()


def detect(text: str, head_only: bool = True) -> str:
    """The license a file states: its SPDX-License-Identifier, else the first rule whose
    sentences all appear (in the first 120 lines when head_only; a LICENSE file is read
    whole). "-or-later" when a notice (not the license text itself) offers any later
    version. "" when none is found."""
    m = SPDX_LINE.search(text)
    if m:
        return m.group(1).strip()
    lines = text.splitlines()
    whole = normalise("\n".join(lines[:120]) if head_only else text)
    # The title lines first: the GPL texts name the Lesser and Affero licenses further down.
    for title, norm in ((True, normalise("\n".join(lines[:TITLE_LINES]))), (False, whole)):
        for spdx, needles in RULES:
            if title and (spdx not in GPL_FAMILY or len(needles) == 1):
                continue                    # only a GPL title with its version settles it early
            if all(n in norm for n in needles):
                if spdx in GPL_FAMILY and LATER in whole and not any(f in whole for f in FULL_TEXT):
                    return spdx + "-or-later"
                return spdx
    return ""


def detect_file(path, text: str, head_only: bool = True) -> str:
    """scancode-toolkit's SPDX expression when it is installed, else the built-in matcher."""
    try:
        from scancode.api import get_licenses       # optional; not a dependency
    except Exception:  # noqa: BLE001  not installed: the built-in matcher
        return detect(text, head_only)
    try:
        found = get_licenses(str(path)) or {}
        return found.get("detected_license_expression_spdx") or detect(text, head_only)
    except Exception:  # noqa: BLE001  a scancode failure falls back too
        return detect(text, head_only)


def parts(expression: str) -> tuple[str, list[str]]:
    """("OR"|"AND", ids) of an SPDX expression; WITH exceptions dropped."""
    expr = re.sub(r"\s+WITH\s+[\w.+-]+", "", expression.replace("(", " ").replace(")", " "))
    if re.search(r"\sOR\s", expr):
        return "OR", [p.strip() for p in re.split(r"\s+OR\s+", expr) if p.strip()]
    return "AND", [p.strip() for p in re.split(r"\s+AND\s+", expr) if p.strip()]


def base(spdx: str) -> tuple[str, bool]:
    """(id without -only/-or-later/+, whether a later version is allowed)."""
    later = spdx.endswith("-or-later") or spdx.endswith("+")
    core = re.sub(r"(-only|-or-later|\+)$", "", spdx)
    return core, later


RANK = {"yes": 0, "conditional": 1, "unknown": 2, "no": 3}


def compatible(file_license: str, repo_license: str) -> tuple[str, str]:
    """Whether a file under file_license may be distributed inside a repository released
    under repo_license: (yes | conditional | no | unknown, why in Simplified Chinese)."""
    if not file_license:
        return "unknown", "没找到这个文件的许可证"
    kind, ids = parts(file_license)
    _, repos = parts(repo_license) if repo_license else ("AND", [""])
    results = [min((one(f, r) for r in repos), key=lambda x: RANK[x[0]]) for f in ids]
    pick = min if kind == "OR" else max
    return pick(results, key=lambda x: RANK[x[0]])


def one(file_license: str, repo_license: str) -> tuple[str, str]:
    f, f_later = base(file_license)
    r, r_later = base(repo_license)
    if not r:
        if f in PERMISSIVE or f.startswith("CC-BY-4"):
            return "conditional", "仓库没有 LICENSE；保留原许可证和版权声明即可"
        return "no", f"{file_license} 要求整个作品用同一许可证发布，而仓库没有 LICENSE"
    if f in PERMISSIVE:
        return "yes", f"{f} 是宽松许可证，保留版权声明即可"
    if f == r and f not in {"CC-BY-NC-4.0"}:
        return "yes", "与仓库许可证相同"
    if f == "Apache-2.0":
        if r in {"GPL-3.0", "AGPL-3.0", "LGPL-3.0", "MPL-2.0"} or (r in {"GPL-2.0", "LGPL-2.1"} and r_later):
            return "yes", "Apache-2.0 可并入这个许可证的作品"
        if r in {"GPL-2.0", "LGPL-2.1", "LGPL-2.0"}:
            return "no", f"Apache-2.0 与 {repo_license} 不兼容"
        return "conditional", "文件仍是 Apache-2.0：保留许可证全文和 NOTICE，注明改动"
    if f == "MPL-2.0":
        if r in GPL_FAMILY:
            return "yes", "MPL-2.0 允许并入 GPL 系作品"
        return "conditional", "MPL-2.0 是文件级 copyleft：这个文件保持 MPL-2.0，改动要公开"
    if f in {"LGPL-2.0", "LGPL-2.1", "LGPL-3.0"}:
        if r in {"GPL-3.0", "AGPL-3.0", "LGPL-3.0"} or (r in {"GPL-2.0", "LGPL-2.1"} and f != "LGPL-3.0"):
            return "yes", "LGPL 代码可并入这个 GPL 系作品"
        return "conditional", "文件保持 LGPL：对它的改动要以 LGPL 公开"
    if f == "GPL-2.0":
        if r == "GPL-2.0" or (f_later and r in {"GPL-3.0", "AGPL-3.0"}):
            return "yes", "GPL 版本相容"
        return "no", f"{file_license} 要求整个作品以 GPL 发布，与仓库的 {repo_license} 冲突"
    if f == "GPL-3.0":
        if r in {"GPL-3.0", "AGPL-3.0"} or (r == "GPL-2.0" and r_later):
            return "yes", "GPL 版本相容"
        return "no", f"{file_license} 要求整个作品以 GPL-3.0 发布，与仓库的 {repo_license} 冲突"
    if f == "AGPL-3.0":
        if r == "GPL-3.0":
            return "conditional", "GPL-3.0 第 13 条允许组合，但这部分仍受 AGPL 约束"
        return "no", f"AGPL-3.0 要求整个作品以 AGPL 发布，与仓库的 {repo_license} 冲突"
    if f.startswith("CC-BY-NC"):
        return "no", "CC BY-NC 禁止商业使用，不能放进开源许可证的仓库"
    if f.startswith("CC-BY-SA"):
        if r == "GPL-3.0":
            return "conditional", "CC BY-SA 4.0 单向兼容 GPL-3.0"
        return "no", f"CC BY-SA 要求以相同许可证发布，与 {repo_license} 冲突"
    if f.startswith("CC-BY"):
        return "conditional", "CC BY 不是软件许可证：保留署名即可"
    if f.startswith("LPPL"):
        return "conditional", "LPPL：改动后的文件要改名并保留原声明"
    return "unknown", f"兼容性表里没有 {file_license}"


__all__ = ["compatible", "detect", "detect_file"]

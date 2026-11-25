"""The anonymous supplementary bundle: the code and extra files of a double-blind
submission, as Anonymous GitHub serves a repository. Working-tree files only (no Git
history), every text file scanned for the authors' names, emails, institutions, home
paths and GitHub accounts, notebooks stripped of outputs and kernel paths, and secrets
and system litter left out. Plain code, no model calls.
"""

import fnmatch
import json
import re
import subprocess
import zipfile
from pathlib import Path

from auto_research.trail import Trail

from ..submission import common
from . import facts

SKIPPED_DIRS = (".git", "drafts", "workflow")
PAPER_EXTS = (".tex", ".bib", ".bbl", ".bst", ".cls", ".sty", ".aux", ".log", ".out", ".blg", ".fls",
              ".fdb_latexmk", ".synctex.gz", ".toc", ".lof", ".lot", ".nav", ".snm")
LITTER = re.compile(r"(?:^|/)(?:\.DS_Store|Thumbs\.db|__pycache__/.*|.*\.py[co]|\.ipynb_checkpoints/.*|"
                    r"\.env(?:\..*)?|\.netrc|\.pypirc|id_(?:rsa|dsa|ecdsa|ed25519)(?:\.pub)?|.*\.(?:pem|key|p12|pfx)|"
                    r"credentials\.json|secrets?\.(?:json|ya?ml|toml)|\.idea/.*|\.vscode/.*)$")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
HOME_PATH = re.compile(r"(/Users/|/home/|[A-Za-z]:\\\\?Users\\\\?)([^/\\\s'\"]+)")
SHARED_HOMES = {"shared", "public", "runner", "user", "username", "anonymous", "root", "ubuntu", "<name>", "$user"}
CODE_HOST = re.compile(r"((?:https?://)?(?:www\.)?(?:github|gitlab|huggingface)\.(?:com|co)/)([A-Za-z0-9][\w.-]*)", re.I)
NEUTRAL_HOSTS = {"anonymous", "orgs", "features", "settings", "login", "topics", "datasets", "spaces", "docs"}
MAX_TEXT = 5_000_000


def anonymous_package(paper_dir: str, include: str = "", out_dir: str = "", fix: str = "list") -> str:
    """Build an anonymized supplementary.zip of the paper's code and extra files.

    paper_dir is the project's Git repository. include is comma-separated globs relative
    to it (a folder name takes everything under it); empty takes the repository's working
    tree (tracked and untracked, .gitignore honoured) minus the paper sources (the files
    the main .tex uses, .tex/.bib/.bbl/.sty/.cls/.bst and LaTeX build files), drafts/,
    workflow/ and .git. Git history is never copied. .DS_Store, __pycache__, .env, keys
    and credentials are dropped. Notebooks lose outputs, execution counts, cell metadata
    and kernel paths. Every text file is scanned for the authors' names (rules.json
    required_authors, venue.md `Authors:`, git log), emails, venue.md `Affiliations:`,
    /Users/<name>/ and /home/<name>/ paths, and GitHub/GitLab/Hugging Face accounts.
    fix="list" (default) lists each with file:line and FAILs without writing the zip;
    fix="replace" replaces them with Anonymous and includes the file. Writes
    supplementary.zip, manifest.md, manifest.json and the trail to out_dir/<timestamp>/
    (default <paper_dir>/drafts/anonymous_package).
    """
    if fix not in ("list", "replace"):
        raise ValueError("fix is list or replace")
    paper = common.repo(paper_dir)
    out = common.run_folder(paper, out_dir, "anonymous_package")
    trail = Trail("anonymous_package", {"paper_dir": str(paper), "include": include, "out_dir": out_dir, "fix": fix}, {})
    files = select(paper, include, out)
    kept, removed = [], []
    for rel in files:
        (removed if LITTER.search(rel) else kept).append(rel)
    terms = identity(paper)
    trail.step("select", files=len(files), kept=len(kept), removed=removed,
               terms={k: len(v) for k, v in terms.items()})

    entries, findings, binary, cleaned = {}, [], [], []
    for rel in kept:
        data = (paper / rel).read_bytes()
        text = _text(data)
        if text is None:
            entries[rel] = data
            binary.append(rel)
            continue
        if rel.endswith(".ipynb"):
            new = clean_notebook(text)
            if new != text:
                cleaned.append(rel)
                text = new
        hits = scan(text, terms)
        for n, kind, match in hits:
            findings.append({"file": rel, "line": n, "kind": kind, "match": match})
        if hits and fix == "replace":
            text = replace(text, terms)
        entries[rel] = text.encode("utf-8")
    trail.step("scan", text=len(kept) - len(binary), binary=len(binary), notebooks=cleaned, findings=len(findings))

    blocked = sorted({f["file"] for f in findings}) if fix == "list" else []
    zip_path = ""
    if not blocked:
        zip_path = str(out / "supplementary.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            for rel, data in entries.items():
                info = zipfile.ZipInfo(rel, date_time=(1980, 1, 1, 0, 0, 0))     # no timestamps that date the work
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                z.writestr(info, data)
    status = "FAIL" if blocked else "PASS_WITH_NOTES" if (findings or binary) else "PASS"
    manifest = {"status": status, "fix": fix, "zip": zip_path, "included": sorted(entries), "removed": removed,
                "blocked": blocked, "binary": binary, "notebooks_cleaned": cleaned, "findings": findings,
                "terms": {k: sorted(v) for k, v in terms.items()},
                "total_bytes": sum(len(d) for d in entries.values())}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "manifest.md").write_text(report(manifest), encoding="utf-8")
    trail.step("bundle", zip=zip_path, blocked=blocked)
    run = trail.save(out, report=out / "manifest.md", manifest=out / "manifest.json", zip=zip_path)
    return json.dumps({"status": status, "report": str(out / "manifest.md"), "run": str(run), "zip": zip_path,
                       "manifest": str(out / "manifest.json"), "included": len(entries), "removed": len(removed),
                       "findings": len(findings), "blocked": blocked, "notebooks_cleaned": cleaned},
                      ensure_ascii=False)


def select(paper: Path, include: str, out: Path) -> list[str]:
    """The working-tree files to bundle, as repository-relative POSIX paths."""
    done = subprocess.run(["git", "-C", str(paper), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                          capture_output=True, text=True, check=True)
    files = sorted({p for p in done.stdout.split("\0") if p and (paper / p).is_file() and not (paper / p).is_symlink()})
    outside = [p for p in files if out not in (paper / p).resolve().parents and p.split("/")[0] != ".git"]
    globs = [g.strip().strip("/") for g in include.split(",") if g.strip()]
    if globs:
        return [p for p in outside if any(fnmatch.fnmatch(p, g) or p.startswith(g + "/") for g in globs)]
    paper_files = set()
    main = common.main_tex(paper)
    if main is not None:
        paper_files = set(common.used_files(paper, main)[0])
        paper_files.add(main.with_suffix(".pdf").relative_to(paper).as_posix())
    return [p for p in outside if p.split("/")[0] not in SKIPPED_DIRS and p not in paper_files
            and not p.endswith(PAPER_EXTS)]


def identity(paper: Path) -> dict[str, set[str]]:
    """What would identify the authors: names, emails, institutions and code-host accounts."""
    names, emails, places, accounts = set(), set(), set(), set()
    for item in facts.rules_json(paper).get("required_authors", []):
        item = str(item)
        if "@" in item:
            emails.add(item.strip())
        elif "\\" not in item and re.fullmatch(r"[^\d{}]{3,60}", item.strip()) and len(item.split()) >= 2:
            names.add(item.strip())
    names |= {n for n in facts.field_list(paper, "Authors") if len(n) >= 3}
    places |= {a for a in facts.field_list(paper, "Affiliations") if len(a) >= 3}
    done = subprocess.run(["git", "-C", str(paper), "log", "--all", "--format=%an%x09%ae%x09%cn%x09%ce"],
                          capture_output=True, text=True)
    for row in done.stdout.splitlines():
        parts = row.split("\t")
        for name, email in ((parts[0], parts[1]), (parts[2], parts[3])) if len(parts) == 4 else ():
            if len(name.strip()) >= 3 and not re.search(r"bot\b|GitHub|noreply", name, re.I):
                names.add(name.strip())
            if "@" in email and "." in email.split("@")[-1]:
                emails.add(email.strip())
                m = re.match(r"(?:\d+\+)?([\w-]+)@users\.noreply\.github\.com$", email)
                if m:
                    accounts.add(m.group(1))
    remote = subprocess.run(["git", "-C", str(paper), "remote", "-v"], capture_output=True, text=True).stdout
    accounts |= {m.group(2) for m in CODE_HOST.finditer(remote.replace(":", "/"))}
    emails = {e for e in emails if not e.endswith("users.noreply.github.com")}
    return {"names": names, "emails": emails, "affiliations": places, "accounts": accounts}


def _text(data: bytes) -> str | None:
    if len(data) > MAX_TEXT or b"\0" in data[:8192]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def clean_notebook(text: str) -> str:
    """The notebook without outputs, execution counts, cell metadata, widget state and the
    kernel's display name; the kernel name and language stay so it still opens."""
    try:
        nb = json.loads(text)
    except ValueError:
        return text
    for c in nb.get("cells", []):
        if c.get("cell_type") == "code":
            c["outputs"] = []
            c["execution_count"] = None
        c["metadata"] = {}
    meta = nb.get("metadata", {})
    kernel = meta.get("kernelspec", {})
    nb["metadata"] = {k: v for k, v in {
        "kernelspec": {"name": "python3", "display_name": "Python 3", "language": kernel.get("language", "python")}
        if kernel else None,
        "language_info": {"name": meta.get("language_info", {}).get("name", "python")} if meta.get("language_info") else None,
    }.items() if v}
    return json.dumps(nb, ensure_ascii=False, indent=1) + "\n"


def _term(term: str) -> re.Pattern:
    return re.compile(r"(?<![\w])" + r"\s+".join(re.escape(w) for w in term.split()) + r"(?![\w])", re.I)


def scan(text: str, terms: dict[str, set[str]]) -> list[tuple[int, str, str]]:
    """(line, kind, matched text) for everything identifying in the text."""
    pats = [(kind, _term(t)) for kind in ("names", "affiliations", "accounts") for t in sorted(terms[kind])]
    known = {e.lower() for e in terms["emails"]}
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        for kind, pat in pats:
            for m in pat.finditer(line):
                hits.append((n, kind[:-1], m.group(0)))
        for m in EMAIL.finditer(line):
            if not m.group(0).lower().endswith(("@example.com", "@example.org", "@anonymous.com")):
                hits.append((n, "email" if m.group(0).lower() not in known else "author_email", m.group(0)))
        for m in HOME_PATH.finditer(line):
            if m.group(2).lower() not in SHARED_HOMES:
                hits.append((n, "home_path", m.group(0)))
        for m in CODE_HOST.finditer(line):
            if m.group(2).lower() not in NEUTRAL_HOSTS:
                hits.append((n, "code_host", m.group(0)))
    seen, out = set(), []
    for h in hits:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


def replace(text: str, terms: dict[str, set[str]]) -> str:
    text = EMAIL.sub(lambda m: m.group(0) if m.group(0).lower().endswith("@example.com") else "anonymous@example.com", text)
    text = HOME_PATH.sub(lambda m: m.group(0) if m.group(2).lower() in SHARED_HOMES else m.group(1) + "anonymous", text)
    text = CODE_HOST.sub(lambda m: m.group(0) if m.group(2).lower() in NEUTRAL_HOSTS else m.group(1) + "anonymous", text)
    for kind in ("names", "affiliations", "accounts"):
        for t in sorted(terms[kind], key=len, reverse=True):
            text = _term(t).sub("Anonymous", text)
    return text


def report(m: dict) -> str:
    title = {"PASS": "可以上传", "PASS_WITH_NOTES": "可以上传，有提示", "FAIL": "不能上传"}[m["status"]]
    lines = [f"# 匿名补充材料：{title}", "",
             f"收入 {len(m['included'])} 个文件共 {common.mb(m['total_bytes'])}，只取工作区文件，不带 Git 历史。"
             + (f" 压缩包：`{m['zip']}`。" if m["zip"] else " 没有生成压缩包。"), ""]
    if m["blocked"]:
        lines += ["## 必须修改", "", "下面的文件含有能认出作者的内容。手动改掉，或用 fix=\"replace\" 自动替换成 Anonymous 后重跑。", ""]
    if m["findings"]:
        verb = "已替换" if m["fix"] == "replace" else "发现"
        lines += [f"## {verb}的身份信息（{len(m['findings'])}）", "", "| 位置 | 类型 | 内容 |", "|---|---|---|"]
        lines += [f"| `{f['file']}:{f['line']}` | {f['kind']} | `{facts.cell(f['match'])}` |" for f in m["findings"]]
        lines.append("")
    if m["notebooks_cleaned"]:
        lines += ["## 已清理的 notebook", "", "清掉了输出、执行序号、单元元数据和 kernel 路径：", ""]
        lines += [f"- `{p}`" for p in m["notebooks_cleaned"]] + [""]
    if m["binary"]:
        lines += ["## 未扫描的二进制文件", "", "无法检查里面的元数据（如 PDF 作者、图片 EXIF），上传前自己确认：", ""]
        lines += [f"- `{p}`" for p in m["binary"]] + [""]
    if m["removed"]:
        lines += ["## 去掉的文件", "", "系统杂项、缓存、密钥和环境变量文件：", ""]
        lines += [f"- `{p}`" for p in m["removed"]] + [""]
    lines += ["## 收入", ""] + ([f"- `{p}`" for p in m["included"]] or ["无"]) + [""]
    lines += ["检查的身份信息：" + "；".join(f"{k} {len(v)} 项" for k, v in m["terms"].items())
              + "。代码里的 README 链接请换成 anonymous.4open.science 的匿名链接。", ""]
    return "\n".join(lines)


__all__ = ["anonymous_package"]

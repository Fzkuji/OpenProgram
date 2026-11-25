"""The arXiv / camera-ready source bundle, built as Google's arxiv_latex_cleaner does:
only the files the main .tex uses, comments stripped, the compiled .bbl included and
everything else (drafts, workflow notes, unused figures, the repository itself) left out.
Plain code, no model calls.
"""

import json
import os
import zipfile
from pathlib import Path

from auto_research.trail import Trail

from . import common

TOTAL_LIMIT = 50_000_000
FIGURE_LIMIT = 10_000_000
SKIPPED_DIRS = (".git", "drafts", "workflow")


def arxiv_package(paper_dir: str, out_dir: str = "") -> str:
    """Build a clean arXiv or camera-ready source bundle of the paper.

    paper_dir is the project's Git repository. Follows the main .tex through \\input,
    \\include, \\includegraphics, \\bibliography, \\bibliographystyle, \\usepackage and
    \\documentclass to the files in the repository, strips LaTeX comments from the .tex
    files and adds <main>.bbl. FAIL when the .bbl or a referenced file is missing (no zip
    is written then). Writes arxiv.zip, manifest.md, manifest.json and the trail to
    out_dir/<timestamp>/ (default <paper_dir>/drafts/arxiv_package).
    """
    paper = common.repo(paper_dir)
    main = common.main_tex(paper)
    if main is None:
        raise ValueError(f"{paper}: no main .tex (set main_tex in workflow/rules.json)")
    out = common.run_folder(paper, out_dir, "arxiv_package")
    trail = Trail("arxiv_package", {"paper_dir": str(paper), "out_dir": out_dir}, {})
    used, missing = common.used_files(paper, main)
    used = {p: k for p, k in used.items() if k != "bib" and p.split("/")[0] not in SKIPPED_DIRS}
    problems = [f"缺少被引用的文件：{m}" for m in missing]
    bbl = main.with_suffix(".bbl")
    if not bbl.is_file():
        problems.insert(0, f"没有 {bbl.name}：先编译一次（latexmk -pdf {main.name}），让 {bbl.name} 生成，再打包。")
    trail.step("follow", main=main.name, used=len(used), missing=missing, bbl=bbl.is_file())

    included = [{"path": p, "kind": k, "bytes": (paper / p).stat().st_size} for p, k in sorted(used.items())]
    dropped = dropped_files(paper, set(used), out)
    total = sum(f["bytes"] for f in included)
    warnings = []
    if total > TOTAL_LIMIT:
        warnings.append(f"文件合计 {common.mb(total)}，超过 arXiv 的 50 MB 上限：压缩大图（位图降到 300 dpi，"
                        "PDF 图用 gs -dPDFSETTINGS=/printer），或删去不必要的图。")
    for f in included:
        if f["kind"] == "figure" and f["bytes"] > FIGURE_LIMIT:
            warnings.append(f"{f['path']} 有 {common.mb(f['bytes'])}：建议压缩（PNG 用 pngquant，"
                            "JPEG 降质量到 85，矢量 PDF 用 gs 重新生成）。")

    zip_path = ""
    if not problems:
        zip_path = str(out / "arxiv.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            for f in included:
                source = paper / f["path"]
                if f["path"].endswith(".tex"):
                    z.writestr(f["path"], common.strip_comments(source.read_text(encoding="utf-8", errors="ignore")))
                else:
                    z.write(source, f["path"])
    trail.step("bundle", included=len(included), dropped=len(dropped), bytes=total, problems=problems,
               warnings=warnings, zip=zip_path)

    status = "FAIL" if problems else "PASS_WITH_NOTES" if warnings else "PASS"
    manifest = {"status": status, "main_tex": main.name, "zip": zip_path, "total_bytes": total,
                "problems": problems, "warnings": warnings, "included": included, "dropped": dropped}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "manifest.md").write_text(report(manifest), encoding="utf-8")
    run = trail.save(out, report=out / "manifest.md", manifest=out / "manifest.json", zip=zip_path)
    return json.dumps({"status": status, "report": str(out / "manifest.md"), "run": str(run), "zip": zip_path,
                       "manifest": str(out / "manifest.json"), "included": len(included), "dropped": len(dropped),
                       "total_bytes": total, "problems": problems, "warnings": warnings}, ensure_ascii=False)


def dropped_files(paper: Path, used: set[str], out: Path) -> list[dict]:
    """Every repository file left out; .git, drafts/ and workflow/ each as one entry."""
    dropped, folders = [], {d: 0 for d in SKIPPED_DIRS}
    for root, dirs, files in os.walk(paper):
        rel_root = Path(root).relative_to(paper)
        top = rel_root.parts[0] if rel_root.parts else ""
        for name in files:
            path = Path(root) / name
            rel = (rel_root / name).as_posix()
            if top in folders:
                folders[top] += path.stat().st_size
            elif rel not in used and out not in path.parents:
                dropped.append({"path": rel, "bytes": path.stat().st_size})
    return [{"path": f"{d}/", "bytes": n} for d, n in folders.items() if n] + sorted(dropped, key=lambda f: f["path"])


def report(m: dict) -> str:
    title = {"PASS": "可以上传", "PASS_WITH_NOTES": "可以上传，有提示", "FAIL": "不能上传"}[m["status"]]
    lines = [f"# arXiv 源码包：{title}", "",
             f"主文件 `{m['main_tex']}`，收入 {len(m['included'])} 个文件共 {common.mb(m['total_bytes'])}。"
             + (f" 压缩包：`{m['zip']}`。" if m["zip"] else " 没有生成压缩包。"), ""]
    if m["problems"]:
        lines += ["## 必须修改", ""] + [f"- {p}" for p in m["problems"]] + [""]
    if m["warnings"]:
        lines += ["## 提示", ""] + [f"- {w}" for w in m["warnings"]] + [""]
    lines += ["## 收入", "", "| 文件 | 类型 | 大小 |", "|---|---|---|"]
    lines += [f"| `{f['path']}` | {f['kind']} | {common.mb(f['bytes'])} |" for f in m["included"]]
    lines += ["", "## 未收入", "", "| 文件 | 大小 |", "|---|---|"]
    lines += [f"| `{f['path']}` | {common.mb(f['bytes'])} |" for f in m["dropped"]] or ["| — | — |"]
    lines += ["", ".tex 文件里的注释已删除（\\% 和 verbatim 类环境保留，comment 环境删除）。"
                  ".bib 不收入：arXiv 用 .bbl 生成参考文献。", ""]
    return "\n".join(lines)


__all__ = ["arxiv_package"]

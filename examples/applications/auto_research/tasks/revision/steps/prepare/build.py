"""Copy the sources, compile them and render the red/green comparison."""

import re
import shutil
import subprocess
import tarfile
import io
from pathlib import Path

ASSETS = Path(__file__).resolve().parent / "assets"
SKIP = shutil.ignore_patterns(".git", "drafts", "*.aux", "*.log", "*.fls", "*.fdb_latexmk",
                              "*.out", "*.blg", "*.synctex.gz")


def run(args: list[str], cwd: Path, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def export_baseline(paper: Path, ref: str, target: Path) -> None:
    """Write the committed tree at `ref` into `target`."""
    archive = subprocess.run(["git", "-C", str(paper), "archive", ref],
                             capture_output=True, timeout=120)
    if archive.returncode:
        raise ValueError(f"Cannot read baseline {ref!r}: {archive.stderr.decode().strip()}")
    target.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        tar.extractall(target, filter="data")


def copy_tree(source: Path, target: Path) -> None:
    shutil.copytree(source, target, ignore=SKIP)


def compile_pdf(root: Path, main: str) -> tuple[bool, str]:
    """Build with latexmk; return success and the LaTeX log."""
    result = run(["latexmk", "-pdf", "-interaction=nonstopmode", "-halt-on-error", main], root)
    log = root / Path(main).with_suffix(".log").name
    text = log.read_text(errors="replace") if log.exists() else result.stdout + result.stderr
    return result.returncode == 0 and (root / Path(main).with_suffix(".pdf").name).exists(), text


def render_diff(base: Path, draft: Path, main: str, target: Path) -> tuple[bool, str]:
    """Green for added text, red strike-through for removed text, framed figures."""
    flat = draft / "_diff.tex"
    result = run(["latexdiff", "--flatten", f"--preamble={ASSETS / 'diff_preamble.tex'}",
                  "--graphics-markup=none", "--math-markup=whole",
                  str(base / main), str(draft / main)], draft)
    if result.returncode:
        return False, "latexdiff failed: " + result.stderr.strip()[-500:]
    flat.write_text(keep_new_tabular(result.stdout))
    for _ in range(3):
        compiled = run(["lualatex", "-interaction=nonstopmode", flat.name], draft)
    pdf = draft / "_diff.pdf"
    if not pdf.exists():
        return False, "Comparison PDF did not compile: " + compiled.stdout[-500:]
    shutil.copy(pdf, target)
    return True, ""


CHANGED_TABULAR = re.compile(
    r"\\DIFdelbegin(?:FL)? (?:%DIFDELCMD < [^\n]*\n)+\\DIFdelend(?:FL)? "
    r"\\DIFaddbegin(?:FL)? (\\(?:begin|end)\{tabular\*?\}[^\n]*\n)\\DIFaddend(?:FL)? ")


def keep_new_tabular(text: str) -> str:
    """A changed tabular opening cannot be marked up; keep the new one so the PDF compiles."""
    return CHANGED_TABULAR.sub(lambda m: m.group(1), text)


def read_files(root: Path, names: tuple[str, ...]) -> dict:
    return {name: (root / name).read_bytes() if (root / name).exists() else None for name in names}

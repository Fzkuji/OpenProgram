"""What method_diagram and caption_check share: the paper text, LaTeX normalization,
images for a model that reads them, and the TeX and poppler tools. Plain code, no model
calls."""

import base64
import re
import shutil
import subprocess
from pathlib import Path

from ..revision.rules import paper as paper_rules
from ..revision.steps.prepare.source import flatten

TIMEOUT = {"pdflatex": 120, "pdftoppm": 60}
SECTION = re.compile(r"\\section\*?\{([^}]*)\}")


def open_paper(paper_dir: str) -> tuple[Path, object, str]:
    """The repository, its rules and the flattened main file (comments removed)."""
    paper = Path(paper_dir).expanduser().resolve()
    if not (paper / ".git").exists():
        raise ValueError(f"{paper} is not a Git repository")
    rules = paper_rules.load(paper)
    if not (paper / rules.main_tex).is_file():
        raise ValueError(f"{paper / rules.main_tex} does not exist")
    return paper, rules, flatten(paper, rules.main_tex)


def document(full: str) -> str:
    """The text from \\begin{document} to \\end{document}, bibliography and back matter included."""
    start = full.find(r"\begin{document}")
    end = full.find(r"\end{document}")
    return full[start if start >= 0 else 0:end if end >= 0 else len(full)]


def norm(text: str) -> str:
    """Text as a reader sees it, for matching quotes: commands, braces, $ and ~ removed,
    whitespace runs as one space, case folded."""
    text = re.sub(r"\\[a-zA-Z]+\*?", " ", text)
    text = re.sub(r"[{}$~]", " ", text).replace("\\", " ")
    return " ".join(text.split()).casefold()


def plain(text: str) -> str:
    """LaTeX as plain words: citations and references dropped, commands unwrapped."""
    text = re.sub(r"\\(?:cite[tp]?|citet|citep|ref|cref|Cref|autoref|label)\*?(?:\[[^\]]*\])?\{[^}]*\}", "", text)
    text = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?", " ", text)
    text = re.sub(r"[{}$]", "", text).replace("~", " ").replace("\\", "")
    return " ".join(text.split())


def which(tool: str) -> str | None:
    return shutil.which(tool)


def image_input(role) -> bool:
    """Whether the model behind a role takes images: a callable runtime receives the
    blocks as they are; a provider model says so in its input modalities."""
    from openprogram.runtime.agent.call_state import _current_runtime
    runtime = getattr(role, "runtime", None) or _current_runtime.get(None)
    if runtime is None:
        return False
    if getattr(runtime, "_call_fn", None) is not None:
        return True
    model = getattr(runtime, "api_model", None)
    return "image" in (getattr(model, "input", None) or [])


def image_block(path: Path) -> dict:
    mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(path.suffix.lower(), "image/png")
    return {"type": "image", "data": base64.b64encode(path.read_bytes()).decode("ascii"), "mime_type": mime}


def rasterize(pdf: Path, png: Path, dpi: int = 200) -> None:
    """The first page of a pdf as a png, by pdftoppm. Raises RuntimeError on failure."""
    if not which("pdftoppm"):
        raise RuntimeError("pdftoppm is not installed (poppler)")
    stem = png.with_suffix("")
    try:
        done = subprocess.run(["pdftoppm", "-png", "-r", str(dpi), "-singlefile", "-f", "1", "-l", "1",
                               str(pdf), str(stem)], capture_output=True, text=True, timeout=TIMEOUT["pdftoppm"])
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("pdftoppm timed out") from e
    if done.returncode != 0 or not png.is_file():
        raise RuntimeError(f"pdftoppm exited {done.returncode}: {' '.join(done.stderr.split())[-200:]}")


def run_pdflatex(tex: Path) -> tuple[bool, str]:
    """Compile tex in its own folder with pdflatex; (ok, the last error lines of the log)."""
    if not which("pdflatex"):
        return False, "pdflatex is not installed"
    try:
        done = subprocess.run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "-no-shell-escape",
                               tex.name], cwd=tex.parent, capture_output=True, text=True,
                              timeout=TIMEOUT["pdflatex"])
    except subprocess.TimeoutExpired:
        return False, f"pdflatex ran longer than {TIMEOUT['pdflatex']} seconds"
    if done.returncode == 0 and tex.with_suffix(".pdf").is_file():
        return True, ""
    errors = [line for line in done.stdout.splitlines() if line.startswith("!") or line.startswith("l.")]
    return False, " ".join(errors[:4])[:400] or f"pdflatex exited {done.returncode}"


__all__ = ["document", "image_block", "image_input", "norm", "open_paper", "plain", "rasterize", "run_pdflatex",
           "which"]

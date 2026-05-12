"""End-to-end sanity checks for openprogram.tools.pdf.figures.

Runs the vector figure extractor on two real academic PDFs and asserts:
  (a) at least the expected number of figures detected
  (b) each bbox is sane (width and height > 50pt)
  (c) every emitted PNG exists and is > 5KB

This is a "real PDFs" test rather than a unit test — it requires the two
local PDFs listed below. If they are missing, the cases are skipped.

Run as a script:
    python -m openprogram.tools.pdf.tests.test_figures
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from openprogram.tools.pdf.figures import ExtractedFigure, extract_figures


_NEURIPS_PDF = Path("/Users/fzkuji/Documents/LLM Uncertainty/0_Latex/0NeurIPS.pdf")
_DSV4_PDF = Path(
    "/Users/fzkuji/Documents/Research-Wiki/Large Language Models/"
    "Architecture/Long-Context Efficiency/deepseekAI2026_deepseekv4/"
    "deepseekAI2026_deepseekv4.pdf"
)


def _check_results(name: str, results: list[ExtractedFigure], min_count: int) -> None:
    assert len(results) >= min_count, (
        f"{name}: expected at least {min_count} figures, got {len(results)}"
    )
    for f in results:
        w = f.bbox[2] - f.bbox[0]
        h = f.bbox[3] - f.bbox[1]
        assert w > 50 and h > 50, (
            f"{name}: {f.figure_label} on p{f.page} has tiny bbox {w:.1f}x{h:.1f}"
        )
        assert f.image_path.exists(), f"{name}: missing PNG {f.image_path}"
        size = f.image_path.stat().st_size
        assert size > 5_000, (
            f"{name}: {f.image_path.name} only {size} bytes (< 5KB)"
        )


def test_neurips_paper() -> int:
    if not _NEURIPS_PDF.exists():
        print(f"SKIP test_neurips_paper: {_NEURIPS_PDF} not found")
        return 0
    out = Path(tempfile.mkdtemp(prefix="figtest_neurips_"))
    try:
        r = extract_figures(_NEURIPS_PDF, out)
        # paper has 9 figures + 8 tables; we expect to catch at least 9 figures
        figures = [f for f in r if f.figure_label.startswith("Figure")]
        _check_results("NeurIPS", r, min_count=9)
        assert len(figures) >= 9, f"NeurIPS: expected 9 figures, got {len(figures)}"
        print(f"PASS test_neurips_paper: {len(r)} items ({len(figures)} figures)")
        return 0
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_deepseek_v4_paper() -> int:
    if not _DSV4_PDF.exists():
        print(f"SKIP test_deepseek_v4_paper: {_DSV4_PDF} not found")
        return 0
    out = Path(tempfile.mkdtemp(prefix="figtest_dsv4_"))
    try:
        r = extract_figures(_DSV4_PDF, out)
        figures = [f for f in r if f.figure_label.startswith("Figure")]
        _check_results("DSv4", r, min_count=8)
        assert len(figures) >= 8, f"DSv4: expected >=8 figures, got {len(figures)}"
        print(f"PASS test_deepseek_v4_paper: {len(r)} items ({len(figures)} figures)")
        return 0
    finally:
        shutil.rmtree(out, ignore_errors=True)


def main() -> int:
    rc = 0
    rc |= test_neurips_paper()
    rc |= test_deepseek_v4_paper()
    return rc


if __name__ == "__main__":
    sys.exit(main())

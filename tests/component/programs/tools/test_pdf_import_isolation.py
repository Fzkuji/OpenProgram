"""Import isolation for optional document runtimes."""

from __future__ import annotations

import subprocess
import sys


def test_pdf_layout_does_not_eagerly_import_pymupdf():
    """Registering document workflows must not load the PDF runtime."""
    script = (
        "import sys; "
        "import openprogram.programs.workflow.document.pdf_layout; "
        "raise SystemExit(1 if 'pymupdf' in sys.modules else 0)"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

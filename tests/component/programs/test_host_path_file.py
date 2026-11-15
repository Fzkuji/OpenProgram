"""An application environment finds the host's packages under any code page.

``openprogram-host.pth`` is read by the interpreter's ``site`` module. Python
3.11 decodes it in the locale encoding and nothing else; later versions try
UTF-8 first. The test writes a host path with a Chinese folder under a GBK
locale in a child process and reads it back the way 3.11's ``site`` does, so
it shows the difference on any interpreter. macOS's ``zh_CN.GBK`` locale
stands in for a Chinese Windows code page; this was not run on Windows.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
LOCALE = "zh_CN.GBK"

_CHILD = r"""
import io, locale, sys
from pathlib import Path
from openprogram.programs._host.ui.catalog import write_host_path_file

assert locale.getencoding().upper() == "GBK", locale.getencoding()
location = Path(sys.argv[1])
host = "C:\\Users\\\u7528\u6237\\OpenProgram\\Lib\\site-packages"
written = write_host_path_file(location, [host])
# site.addpackage in Python 3.11.
with io.TextIOWrapper(io.open_code(str(written)), encoding="locale") as handle:
    lines = [line.rstrip() for line in handle]
assert lines == [host], ascii(lines)
"""


def _locale_exists() -> bool:
    if sys.platform == "win32":
        return False
    probe = subprocess.run(
        [sys.executable, "-c", "import locale; print(locale.getencoding())"],
        env={**os.environ, "LC_ALL": LOCALE, "PYTHONUTF8": "0"},
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return probe.stdout.strip().upper() == "GBK"


@pytest.mark.skipif(not _locale_exists(), reason=f"the {LOCALE} locale is not installed")
def test_a_host_path_outside_ascii_is_read_back_in_the_locale_encoding(tmp_path):
    result = subprocess.run(
        [sys.executable, "-c", _CHILD, str(tmp_path)],
        cwd=ROOT, env={**os.environ, "LC_ALL": LOCALE, "PYTHONUTF8": "0", "PYTHONPATH": str(ROOT)},
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, result.stderr

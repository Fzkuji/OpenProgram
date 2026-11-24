"""Loading the Programs imports no HTTP client, MCP SDK or web framework.

Every command that lists or runs a Program, and each standalone run, loads
them; these libraries took a quarter of a second of that and are needed
only when a tool first makes a request.
"""
from __future__ import annotations

import json
import subprocess
import sys

HEAVY = ("httpx", "httpcore", "mcp", "fastapi", "openai", "anthropic")


def test_loading_the_programs_imports_no_network_client(tmp_path):
    code = ("import json, sys; import openprogram.programs; "
            f"print(json.dumps([name for name in {HEAVY!r} if name in sys.modules]))")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120,
                          env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"})
    assert done.returncode == 0, done.stderr[-2000:]
    assert json.loads(done.stdout.strip().splitlines()[-1]) == []

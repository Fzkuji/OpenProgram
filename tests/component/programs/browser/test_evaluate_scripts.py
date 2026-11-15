"""The function ``evaluate`` sends to a page, run in a JavaScript engine (node, the page's V8)."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from openprogram.programs.applications.browser import page_actions as pa

# Each generated function is called as the page would call it, against a stand-in document.
_HARNESS = r"""
const sources = JSON.parse(require("fs").readFileSync(0, "utf8"));
globalThis.counter = 0;
globalThis.document = {title: "T", querySelector: () => ({click() { globalThis.counter++; }})};
(async () => {
  const out = [];
  for (const source of sources) {
    try { out.push({value: await (0, eval)(source)()}); }
    catch (e) { out.push({error: `${e.name}: ${e.message}`}); }
  }
  console.log(JSON.stringify({out, counter: globalThis.counter}));
})();
"""


def test_evaluate_scripts_run_once_and_answer_with_their_last_value(monkeypatch):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    monkeypatch.setattr(pa, "SCRIPT_SECONDS", 0.1)
    scripts = ["document.title", "document.querySelector('#x').click(); 'clicked'", "({a: 1})", "let x = 2; x * 3",
               "await Promise.resolve(5)", "const v = await Promise.resolve(7); return v + 1", "return 9",
               "counter++; counter", "x = 1", "() => 'fn'", "async () => { await null; return 'afn' }",
               "new Promise(() => {})", "this is not js", "JSON.parse('{bad')"]
    sources = [pa.evaluate_source({"script": script}) for script in scripts]
    done = subprocess.run([node, "-e", _HARNESS], input=json.dumps(sources), capture_output=True, text=True,
                          check=True)
    result = json.loads(done.stdout)
    values = [row.get("value") for row in result["out"]]
    assert values[:11] == ["T", "clicked", {"a": 1}, 6, 5, 8, 9, 2, 1, "fn", "afn"]
    assert result["counter"] == 2, "a click and an increment, each run once"
    errors = [row.get("error") for row in result["out"][11:]]
    assert errors[0] == "Error: the script did not finish within 0.1 s"
    assert errors[1].startswith("SyntaxError:") and errors[2].startswith("SyntaxError:")

"""The calculator's evaluator (ui/calc.js), run under Node.

The app has no backend: every result comes from calc.js in the page, so
the tests drive that module directly. They skip when Node is not installed.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

RUNNER = """
const calc = await import(process.argv[1]);
const cases = JSON.parse(process.argv[2]);
const out = cases.map(([source, options]) => {
  try {
    const value = calc.evaluate(source, options || {});
    return { value, formatted: calc.format(value), plain: calc.plain(value) };
  } catch (error) {
    return { error: error instanceof calc.CalcError ? "CalcError" : error.name, message: error.message };
  }
});
out.push({ open: ["(1+(2", "((3))", "2)"].map(calc.openParens) });
console.log(JSON.stringify(out));
"""


def run(cases):
    completed = subprocess.run(
        [NODE, "--no-warnings", "--input-type=module", "-e", RUNNER,
         (APP / "ui" / "calc.js").as_uri(), json.dumps(cases)],
        capture_output=True, text=True, timeout=30, check=True)
    return json.loads(completed.stdout)


def test_manifest():
    manifest = json.loads((APP / "application.json").read_text())
    assert manifest["id"] == "example.calculator"
    assert manifest["ui"]["root"] == "ui" and (APP / "ui" / manifest["ui"].get("entry", "index.html")).is_file()
    assert manifest["operations"] == {} and "backend" not in manifest


@pytest.mark.parametrize("source,expected", [
    ("2+3×4", 14), ("(2+3)*4", 20), ("2^3^2", 512), ("-2^2", -4), ("2^-1", 0.5),
    ("√16+2^10", 1028), ("sqrt(2)^2", 2), ("5²", 25), ("5!", 120),
    ("200+10%", 220), ("200−10%", 180), ("50%", 0.5), ("200×10%", 20),
    ("2(3)", 6), ("2π", 6.283185307179586), ("1E3", 1000), ("0.1+0.2", 0.3),
    ("sin(90)", 1), ("cos(0)", 1), ("ln(e)", 1), ("log(1000)", 3), ("abs(−4)", 4),
    ("10÷4", 2.5), ("7−10", -3),
])
def test_evaluate(source, expected):
    result = run([[source, {"degrees": True}]])[0]
    assert "error" not in result, result
    assert result["value"] == pytest.approx(expected, rel=1e-12)


def test_radians_and_ans():
    deg, rad, ans = run([["sin(90)", {"degrees": True}], ["sin(π/2)", {"degrees": False}], ["Ans×2", {"ans": 21}]])[:3]
    assert deg["value"] == pytest.approx(1) and rad["value"] == pytest.approx(1) and ans["value"] == 42


@pytest.mark.parametrize("source", ["1/0", "(1+2", "2+", "sqrt(-1)", "foo(2)", "1..2", ""])
def test_errors_are_calc_errors(source):
    result = run([[source, {}]])[0]
    assert result.get("error") == "CalcError", result
    assert result["message"]


def test_format_and_open_parens():
    results = run([["1234567.5", {}], ["1/3", {}]])
    assert results[0]["formatted"] == "1,234,567.5" and results[0]["plain"] == "1234567.5"
    assert results[1]["plain"].startswith("0.333333333333")
    assert results[-1]["open"] == [2, 0, 0]

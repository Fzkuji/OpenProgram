import subprocess
import sys

import pytest

TEXT = "The cat sat. The cat, the mat!\napple apple banana\n"


def run(*args, cwd=None):
    return subprocess.run([sys.executable, "tally.py", *args],
                          capture_output=True, text=True, cwd=cwd)


@pytest.fixture
def f(tmp_path):
    p = tmp_path / "in.txt"
    p.write_text(TEXT)
    return str(p)


def test_count_lines(f):
    r = run("count", f)
    assert r.returncode == 0
    assert r.stdout.strip() == "2 lines"


def test_count_words(f):
    r = run("count", f, "--words")
    assert r.stdout.strip() == "10 words"


def test_top(f):
    r = run("top", f, "-n", "3")
    assert r.returncode == 0
    assert r.stdout.split() == ["the", "3", "apple", "2", "cat", "2"]


def test_no_args():
    r = run()
    assert r.returncode == 2
    assert r.stderr.strip()


def test_missing_file():
    r = run("count", "/nope/nope.txt")
    assert r.returncode == 1
    assert "no such file" in r.stderr

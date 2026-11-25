"""Hashes, provenance, winnowing, the license matcher and table, and the archive client's
rate-limit handling, in plain code."""

import io
import subprocess
import urllib.error

from auto_research.tasks.codeorigin import files as fl
from auto_research.tasks.codeorigin import licenses as lic
from auto_research.tasks.codeorigin import swh
from auto_research.tasks.codeorigin.workflow import check_candidates, paths_to_try

from .repo import BSD3, GPL, MIT, OPS, TRAIN


def test_the_blob_hash_is_gits(tmp_path):
    path = tmp_path / "a.py"
    path.write_bytes(b"print('hi')\n")
    git = subprocess.run(["git", "hash-object", str(path)], capture_output=True, text=True).stdout.strip()
    assert fl.sha1_git(path.read_bytes()) == git and fl.swhid(git) == f"swh:1:cnt:{git}"


def test_provenance_vendored_roots_and_repository_urls():
    text = "# utils\n# Adapted from\n#   https://github.com/a/b/blob/main/src/x.py.\nx = 1  # based on the loss\n" \
           "# Source: https://gitlab.com/g/r/-/blob/dev/y.py\n"
    found = fl.provenance(text)
    assert [(p["line"], p["strong"], p["urls"]) for p in found] == [
        (2, True, ["https://github.com/a/b/blob/main/src/x.py"]),
        (5, False, ["https://gitlab.com/g/r/-/blob/dev/y.py"])]
    assert fl.repository("https://github.com/a/b/blob/main/src/x.py") == ("https://github.com/a/b", "src/x.py")
    assert fl.repository("https://gitlab.com/g/r/-/blob/dev/y.py") == ("https://gitlab.com/g/r", "y.py")
    assert fl.repository("https://raw.githubusercontent.com/o/r/main/p/q.py") == ("https://github.com/o/r", "p/q.py")
    assert fl.repository("git@github.com:o/r.git") == ("https://github.com/o/r", "")
    assert fl.repository("https://example.org/x") == ("", "")
    roots = fl.vendor_roots(["third_party/lib/a.py", "vendor/b.py", "pkg/sub/LICENSE", "LICENSE", "src/c.py"])
    assert roots == ["pkg/sub", "third_party/lib", "vendor"]
    assert fl.vendor_root("third_party/lib/x/y.py", roots) == "third_party/lib" and fl.vendor_root("src/c.py", roots) == ""
    assert paths_to_try("third_party/lib/x/y.py", "", "third_party/lib") == ["x/y.py", "y.py"]
    assert paths_to_try("a/b/c.py", "src/c.py", "") == ["src/c.py", "a/b/c.py", "b/c.py"]


def test_winnowing_finds_pasted_code_and_ignores_shared_headers():
    a, b = fl.fingerprints(OPS), fl.fingerprints(TRAIN)
    share, shared = fl.similarity(a, b)
    assert share >= fl.ADAPTED and shared >= fl.MIN_SHARED
    other = "import os\n\n\ndef walk(root):\n    for d, _, files in os.walk(root):\n        yield from files\n" * 2
    assert fl.similarity(a, fl.fingerprints(other))[0] < 0.2
    header = "\n".join("# " + line for line in MIT.splitlines())
    assert fl.similarity(fl.fingerprints(header + "\nx = compute_something_long_enough(1, 2, 3)\n"),
                         fl.fingerprints(header + "\ny = totally_different_function_name(4)\n"))[1] == 0


def test_the_license_matcher():
    assert lic.detect("// SPDX-License-Identifier: Apache-2.0 OR MIT\n") == "Apache-2.0 OR MIT"
    assert lic.detect(GPL) == "GPL-3.0-or-later"
    assert lic.detect(MIT, head_only=False) == "MIT" and lic.detect(BSD3, head_only=False) == "BSD-3-Clause"
    full_gpl2 = "GNU GENERAL PUBLIC LICENSE\nVersion 2, June 1991\n" + "text\n" * 20 + \
        "TERMS AND CONDITIONS\nor (at your option) any later version\nuse the GNU Library General Public License\n"
    assert lic.detect(full_gpl2, head_only=False) == "GPL-2.0"
    mpl = "Mozilla Public License Version 2.0\n...\nGNU Affero General Public License\n"
    assert lic.detect(mpl, head_only=False) == "MPL-2.0"
    assert lic.detect("print('no license here')\n") == ""


def test_the_compatibility_table():
    want = {("MIT", "Apache-2.0"): "yes", ("GPL-3.0", "MIT"): "no", ("GPL-2.0", "GPL-3.0"): "no",
            ("GPL-2.0-or-later", "GPL-3.0"): "yes", ("Apache-2.0", "GPL-2.0"): "no",
            ("Apache-2.0", "GPL-3.0-only"): "yes", ("Apache-2.0", "MIT"): "conditional",
            ("LGPL-2.1", "MIT"): "conditional", ("MPL-2.0", "BSD-3-Clause"): "conditional",
            ("CC-BY-NC-4.0", "MIT"): "no", ("CC-BY-4.0", "MIT"): "conditional", ("AGPL-3.0", "Apache-2.0"): "no",
            ("", "MIT"): "unknown", ("MIT", ""): "conditional", ("GPL-3.0", ""): "no",
            ("GPL-3.0 OR MIT", "Apache-2.0"): "yes", ("MIT AND GPL-3.0", "MIT"): "no", ("WTFPL", "MIT"): "unknown"}
    assert {k: lic.compatible(*k)[0] for k in want} == want


def test_identifier_candidates_are_filtered():
    reply = {"files": [{"file": "a.py", "candidates": [{"origin": "https://github.com/o/r/blob/main/src/a.py", "path": ""},
                                                       {"origin": "not a url", "path": "a.py"},
                                                       {"origin": "https://github.com/o/r", "path": "../etc/passwd"}]},
                       {"file": "unknown.py", "candidates": [{"origin": "https://github.com/o/r", "path": "x"}]}]}
    assert check_candidates(reply, {"a.py"}) == {"a.py": [{"origin": "https://github.com/o/r", "path": "src/a.py"}]}


class Headers(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def test_the_archive_stops_at_the_rate_limit_without_waiting_an_hour(monkeypatch):
    calls = []

    def urlopen(req, timeout=0):
        calls.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests",
                                     Headers({"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(10 ** 10)}),
                                     io.BytesIO(b""))

    monkeypatch.setattr(swh.urllib.request, "urlopen", urlopen)
    archive = swh.Archive()
    assert archive.known(["swh:1:cnt:" + "0" * 40]) is None
    assert "每小时上限" in archive.problem and len(calls) == 1
    assert archive.revision("https://github.com/o/r") == "" and len(calls) == 1      # no more requests


def test_the_archive_reads_known_in_batches(monkeypatch):
    sent = []

    class Reply(io.BytesIO):
        headers = Headers({"X-RateLimit-Remaining": "100", "X-RateLimit-Reset": "0"})

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout=0):
        import json
        ids = json.loads(req.data)
        sent.append(len(ids))
        return Reply(json.dumps({i: {"known": i.endswith("1")} for i in ids}).encode())

    monkeypatch.setattr(swh.urllib.request, "urlopen", urlopen)
    ids = [f"swh:1:cnt:{i:040d}" for i in range(1500)]
    out = swh.Archive().known(ids)
    assert sent == [1000, 500] and sum(out.values()) == 150 and not swh.Archive().problem

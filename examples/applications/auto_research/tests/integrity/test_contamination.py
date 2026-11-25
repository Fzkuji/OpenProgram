"""contamination_check against a fake infini-gram: no network, no pauses."""

import json

import pytest

from auto_research.tasks.integrity import contamination, contamination_check

from .conftest import make_repo

SEEN = "the quick brown fox jumps over the lazy dog near the river bank today"


def items(k: int, seen: int) -> list[dict]:
    rows = []
    for i in range(k):
        question = (SEEN + f" and then item {i} asks something") if i < seen else \
            f"unique question number {i} about rare words alpha beta gamma delta epsilon zeta eta theta iota kappa"
        rows.append({"id": i, "label": "A", "question": question})
    return rows


@pytest.fixture
def fake_api(monkeypatch):
    calls = []

    def post(payload):
        calls.append(payload)
        assert payload["query_type"] == "count"
        if payload["index"] == "bad_index":
            return {"error": "Invalid index"}
        return {"count": 7 if payload["query"].startswith("the quick brown") else 0, "approx": False}

    monkeypatch.setattr(contamination, "post", post)
    monkeypatch.setattr(contamination.time, "sleep", lambda s: None)
    return calls


def write_jsonl(repo, rows):
    (repo / "data").mkdir(exist_ok=True)
    (repo / "data" / "test.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")


def test_counts_rate_ci_and_the_datasheet_paragraph(tmp_path, fake_api):
    repo = make_repo(tmp_path, "\\documentclass{article}\n")
    write_jsonl(repo, items(40, 2))
    result = json.loads(contamination_check(str(repo), "data/test.jsonl", sample="40"))
    assert result["field"] == "question"      # the longest text column
    s = result["summary"]["v4_rpj_llama_s4"]
    assert s["checked"] == 40 and s["contaminated"] == 2 and s["rate"] == 0.05
    assert 0 < s["ci95"][0] < 0.05 < s["ci95"][1]
    assert result["status"] == "PASS_WITH_NOTES"
    assert len(fake_api) <= 40 * contamination.WINDOWS
    data = json.loads(open(result["findings"], encoding="utf-8").read())
    hit = [it for it in data["items"] if it["contaminated"]["v4_rpj_llama_s4"]]
    assert len(hit) == 2 and hit[0]["counts"]["v4_rpj_llama_s4"][0]["count"] == 7
    paragraph = open(result["paragraph"], encoding="utf-8").read()
    assert paragraph.startswith("## Known contamination") and "2 of 40" in paragraph and "v4_rpj_llama_s4" in paragraph


def test_over_ten_percent_fails_and_every_index_is_listed(tmp_path, fake_api):
    repo = make_repo(tmp_path, "\\documentclass{article}\n")
    rows = items(20, 5)
    (repo / "test.csv").write_text("id,question\n" + "\n".join(f"{r['id']},{r['question']}" for r in rows))
    result = json.loads(contamination_check(str(repo), "test.csv", field="question", n="8", sample="20",
                                            index="v4_rpj_llama_s4, v4_dolma-v1_7_llama"))
    assert result["status"] == "FAIL"
    assert result["indexes"] == ["v4_rpj_llama_s4", "v4_dolma-v1_7_llama"]
    assert all(s["rate"] == 0.25 for s in result["summary"].values())


def test_an_unknown_index_is_unchecked_not_clean(tmp_path, fake_api):
    repo = make_repo(tmp_path, "\\documentclass{article}\n")
    write_jsonl(repo, items(10, 0))
    result = json.loads(contamination_check(str(repo), "data/test.jsonl", sample="10", index="bad_index"))
    s = result["summary"]["bad_index"]
    assert s["checked"] == 0 and s["stopped"] == "Invalid index"
    assert result["status"] == "PASS_WITH_NOTES"
    assert len(fake_api) == contamination.GIVE_UP     # stops instead of spending the budget


def test_clean_data_passes_and_bad_input_is_refused(tmp_path, fake_api):
    repo = make_repo(tmp_path, "\\documentclass{article}\n")
    write_jsonl(repo, items(5, 0))
    assert json.loads(contamination_check(str(repo), "data/test.jsonl"))["status"] == "PASS"
    with pytest.raises(ValueError):
        contamination_check(str(repo), "missing.jsonl")


def test_windows_and_loaders(tmp_path):
    assert contamination.windows("a b c", 13) == ["a b c"]
    toks = " ".join(str(i) for i in range(30))
    w = contamination.windows(toks, 13)
    assert len(w) == 3 and w[0].startswith("0 ") and w[-1].endswith(" 29")
    path = tmp_path / "x.json"
    path.write_text(json.dumps({"meta": 1, "data": [{"q": "short", "context": "a much longer passage of text"}]}))
    rows = contamination.load_rows(path)
    assert contamination.guess_field(rows) == "context"

"""The score model trained on OpenReview submissions: hashing, training on a planted signal,
saving and loading, collecting through a fake fetch, and how paper_review uses it. No test
reaches the network or ~/.cache."""

import json
import random

import numpy as np
import pytest

from auto_research.tasks.review import anchors as openreview
from auto_research.tasks.review import predictor as sp
from auto_research.tasks.review import workflow as wf

from .test_workflow import TITLED, paper, review, run, script_roles  # noqa: F401  fixtures and helpers

REAL_COLLECT = sp.collect


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(openreview, "fetch", lambda url, params: calls.append(params))
    monkeypatch.setattr(sp, "CACHE", tmp_path / "model_cache")
    monkeypatch.setattr(sp, "collect", lambda **kw: [])
    monkeypatch.setattr(sp, "PAUSE", 0)
    return calls


def planted(n=1200, seed=0):
    """Rows whose rating and decision follow a few planted words, in noise."""
    rng = random.Random(seed)
    vocab = [f"term{i}" for i in range(2000)]
    rows = []
    for _ in range(n):
        good = rng.random() < 0.45
        words = rng.sample(vocab, 60) + (["provable", "guarantee", "scalable"] if good else ["toy", "heuristic"])
        rng.shuffle(words)
        rows.append({"title": " ".join(words[:8]), "abstract": " ".join(words[8:]),
                     "rating": round((6.2 if good else 4.2) + rng.gauss(0, 1.0), 2),
                     "decision": "accept" if (good and rng.random() < 0.85) or rng.random() < 0.1 else "reject",
                     "group": "ICLR.cc/2025/Conference"})
    return rows


def test_hashing_is_deterministic_and_rows_are_normalised():
    assert sp.bucket("language model") == sp.bucket("language model")
    sp._BUCKET.clear()
    first = sp.bucket("diffusion")
    sp._BUCKET.clear()
    assert sp.bucket("diffusion") == first and 0 <= first[0] < sp.DIM and first[1] in (1.0, -1.0)
    assert sp.terms("Graph Neural-Network models") == ["graph", "neural-network", "models", "graph neural-network",
                                                      "neural-network models"]
    X = sp.features(["a b a c", "", "a b a c"])
    assert X.n == 3 and list(np.diff(X.indptr)) == [6, 0, 6]
    row = X.data[X.indptr[0]:X.indptr[1]]
    assert abs(np.sqrt((row ** 2).sum()) - 1) < 1e-9
    assert np.array_equal(X.indices[:6], X.indices[6:]) and np.allclose(X.data[:6], X.data[6:])


def test_the_model_recovers_a_planted_signal_and_round_trips(tmp_path):
    meta = sp.train(planted(), cache=tmp_path, groups=["ICLR.cc/2024/Conference", "ICLR.cc/2025/Conference"])
    m = meta["metrics"]
    assert m["holdout"] == 180 and m["rating_r"] > 0.5 and m["rating_mae"] < m["baseline_mae"]
    assert m["accept_auc"] > 0.75
    assert {p.name for p in tmp_path.iterdir()} == {"model-2024-2025.json", "model-2024-2025.rating.npy",
                                                   "model-2024-2025.accept.npy"}
    saved = json.loads((tmp_path / "model-2024-2025.json").read_text())
    assert saved["dims"] == sp.DIM and saved["rows"] == 1200 and saved["trained_on"] == "1200 ICLR submissions (2024–2025)"
    assert "w_rating" not in saved and saved["metrics"] == m
    loaded = sp.load(tmp_path)
    good = sp.score(loaded, "provable guarantee", "a scalable method with a provable guarantee")
    bad = sp.score(loaded, "toy heuristic", "a toy heuristic")
    assert good["rating"] > bad["rating"] and good["accept_probability"] > bad["accept_probability"]
    assert good == sp.score(meta, "provable guarantee", "a scalable method with a provable guarantee")
    assert sp.predict("provable guarantee", "a scalable method with a provable guarantee", cache=tmp_path) == good


def test_predict_trains_only_when_allowed_and_with_enough_rows(monkeypatch, tmp_path):
    assert sp.predict("t", "a", cache=tmp_path) is None                         # no model, not allowed
    assert sp.predict("t", "a", allow_train=True, cache=tmp_path) is None       # collect found nothing
    monkeypatch.setattr(sp, "collect", lambda **kw: planted(400))
    out = sp.predict("provable", "guarantee", allow_train=True, cache=tmp_path)
    assert out["rows"] == 400 and 1 <= out["rating"] <= 10 and sp.load(tmp_path) is not None


def test_collect_joins_submissions_and_reviews_and_caches(isolated, tmp_path):
    group = "ICLR.cc/2026/Conference"

    def note(forum, **content):
        return {"id": forum, "forum": forum, "content": {k: {"value": v} for k, v in content.items()}}

    def review_note(rid, forum, rating):
        return {"id": rid, "forum": forum, "invitations": [f"{group}/Submission1/-/Official_Review"],
                "content": {"rating": {"value": rating}}}
    forums = {"notes": [note("a", title="Accepted {Paper}", abstract="good  stuff", venueid=group),
                        note("r", title="Rejected", abstract="meh", venueid=f"{group}/Rejected_Submission"),
                        note("w", title="Withdrawn", abstract="x", venueid=f"{group}/Withdrawn_Submission"),
                        note("n", title="No reviews", abstract="y", venueid=group)]}
    replies = {"notes": [review_note("ra1", "a", 8), review_note("ra2", "a", 6), review_note("rr1", "r", 2),
                         review_note("rw1", "w", 4), "not a note"]}
    seen = []

    def fetch(url, params):
        seen.append(params)
        return forums if params["source"] == "forum" else replies
    openreview.fetch = fetch                                                   # restored by monkeypatch
    rows = REAL_COLLECT(venues=[group], queries=["q1", "q2"], cache=tmp_path, log=lambda *_: None)
    assert len(seen) == 4 and {p["limit"] for p in seen} == {sp.LIMIT}
    by = {r["forum"]: r for r in rows}
    assert set(by) == {"a", "r"}                                                # decided, reviewed
    assert by["a"]["title"] == "Accepted Paper" and by["a"]["abstract"] == "good stuff"
    assert by["a"]["decision"] == "accept" and by["r"]["decision"] == "reject"
    assert by["a"]["rating"] == pytest.approx((8.2 + 6.4) / 2)                  # 0-10 mapped onto 1-10
    assert (tmp_path / "dataset-2026-2026.jsonl").exists()
    assert REAL_COLLECT(cache=tmp_path, log=lambda *_: None) == rows and len(seen) == 4   # cached, no request


def test_paper_review_with_predictor_off_makes_no_request(paper, isolated, monkeypatch):
    (paper / "0main.tex").write_text(TITLED)
    monkeypatch.setattr(sp, "predict", lambda *a, **k: pytest.fail("predict was called"))
    with script_roles({wf.reviewer: [review()]}):
        out = run(paper, calibration="off", predictor="off")
    assert isolated == [] and out["score_model"] is None
    record = json.load(open(out["review"], encoding="utf-8"))
    assert record["score_model"] is None and "训练出的评分模型" not in open(out["report"], encoding="utf-8").read()
    with pytest.raises(ValueError, match="predictor"):
        run(paper, predictor="always")


def test_paper_review_reports_the_trained_model(paper, monkeypatch):
    (paper / "0main.tex").write_text(TITLED)
    seen = {}

    def predict(title, abstract, allow_train=False):
        seen.update(title=title, abstract=abstract, allow_train=allow_train)
        return {"rating": 5.4, "accept_probability": 0.31, "scale": [1, 10], "rows": 3000, "date": "2026-11-24",
                "reads": "title and abstract", "trained_on": "3000 ICLR submissions (2024–2026)",
                "metrics": {"holdout": 450, "rating_r": 0.41, "rating_mae": 1.02, "baseline_mae": 1.12,
                            "accept_auc": 0.68, "baseline_auc": 0.5}}
    monkeypatch.setattr(sp, "predict", predict)
    with script_roles({wf.reviewer: [review()]}):
        out = run(paper, calibration="off")
    assert seen["title"] and seen["allow_train"] is False                       # calibration off: no training
    assert out["score_model"]["rating"] == 5.4 and out["score_model"]["trained_on"].startswith("3000 ICLR")
    record = json.load(open(out["review"], encoding="utf-8"))
    assert set(record["score_model"]) >= {"rating", "accept_probability", "metrics", "trained_on", "date"}
    text = open(out["report"], encoding="utf-8").read()
    assert "## 训练出的评分模型" in text and "3000 ICLR submissions" in text and "r=0.41" in text
    assert "AUC=0.68" in text and "只看标题和摘要" in text and "31%" in text


def test_a_failing_model_never_stops_the_review(paper, monkeypatch):
    (paper / "0main.tex").write_text(TITLED)
    monkeypatch.setattr(sp, "predict", lambda *a, **k: 1 / 0)
    with script_roles({wf.reviewer: [review()]}):
        out = run(paper)
    assert out["score_model"] is None and out["status"]

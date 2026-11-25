"""A score predictor trained on OpenReview submissions: the small, local counterpart of
CycleReviewer and OpenReviewer, which are reviewer models trained on OpenReview data.

It reads only the title and abstract. collect() gathers (title, abstract, mean rating,
decision) for ICLR submissions of the latest decided years (ICLR publishes the reviews of
rejected papers too) through the two search endpoints anchors.py uses: source=forum for
submissions and source=reply for their reviews, over a set of broad ML queries, joined by
forum. train() fits, in numpy only, a ridge regression of the mean rating (on the
family's 1 to 10 scale) and a logistic regression of accept on hashed unigrams and bigrams
(2^18 signed buckets, sublinear tf, l2-normalised rows), holding out 15% to report
Pearson r and MAE against a mean baseline, and AUC. The dataset and the model live in
CACHE (outside any repository), so they are built once per machine.

A submission's mean rating is over the reviews the reply searches returned, which can be
fewer than all its reviews.
"""

import hashlib
import json
import math
import re
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from . import anchors

CACHE = Path.home() / ".cache" / "auto_research" / "openreview"
BITS = 18
DIM = 1 << BITS
HOLDOUT = 0.15
YEARS = 3                     # latest decided ICLR years
LIMIT = 1000                  # notes per search request
PAUSE = 1.0                   # seconds between requests
MIN_ROWS = 200                # fewer rows than this are not worth a model
FAMILY = (1, 10)
QUERIES = (
    "language model", "large language model", "reinforcement learning", "diffusion", "graph neural network",
    "optimization", "benchmark", "transformer", "attention", "generative model", "contrastive learning",
    "self-supervised", "representation learning", "federated learning", "robustness", "adversarial",
    "privacy", "fairness", "interpretability", "causal", "bayesian", "uncertainty", "theory", "generalization",
    "meta-learning", "continual learning", "few-shot", "vision", "image", "video", "3d", "speech", "multimodal",
    "time series", "molecule", "protein", "neural network", "kernel", "sparse", "quantization", "pruning",
    "distillation", "agent", "reasoning", "alignment", "in-context learning", "fine-tuning", "dataset",
    "physics", "control", "planning", "sampling", "efficient", "recommendation",
)
TOKEN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


# ---- collecting --------------------------------------------------------------

def _decided(group: str, query: str) -> bool:
    found = anchors.fetch(anchors.API, {"term": query, "group": group, "source": "forum", "limit": 25})
    ids = [str(anchors._value(n, "venueid") or "") for n in (found or {}).get("notes") or []]
    return any(v == group or v.endswith("/Rejected_Submission") for v in ids)


def latest_groups(query: str, year: int | None = None, years: int = YEARS) -> list[str]:
    """The latest ICLR groups with decisions, newest first."""
    this = year or datetime.now().year
    out = []
    for y in range(this + 1, this - 6, -1):
        group = f"ICLR.cc/{y}/Conference"
        if _decided(group, query):
            out.append(group)
            if len(out) == years:
                break
        time.sleep(PAUSE)
    return out


def _year(group: str) -> int:
    return int(re.search(r"/(\d{4})/", group).group(1))


def dataset_path(groups: list[str], cache: Path | None = None) -> Path:
    years = sorted(_year(g) for g in groups)
    return Path(cache or CACHE) / f"dataset-{years[0]}-{years[-1]}.jsonl"


def cached_dataset(cache: Path | None = None) -> Path | None:
    found = sorted(Path(cache or CACHE).glob("dataset-*.jsonl"))
    return found[-1] if found else None


def read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def join(submissions: dict, reviews: dict, decisions: dict) -> list[dict]:
    """Rows of submissions that have a decision, a title and abstract, and a rated review."""
    rows = []
    for forum, s in submissions.items():
        group, venueid = s["group"], s["venueid"]
        if decisions.get(forum):
            decision = "accept" if decisions[forum].lower().startswith("accept") else "reject"
        elif venueid == group:
            decision = "accept"
        elif venueid.endswith("/Rejected_Submission"):
            decision = "reject"
        else:
            continue
        ratings = list((reviews.get(forum) or {}).values())
        if not ratings or not s["title"] or not s["abstract"]:
            continue
        rows.append({"forum": forum, "group": group, "title": s["title"], "abstract": s["abstract"],
                     "ratings": ratings, "rating": round(sum(ratings) / len(ratings), 3), "decision": decision})
    return rows


def collect(venues: list[str] | None = None, queries=QUERIES, limit: int = LIMIT, cache: Path | None = None,
            refresh: bool = False, budget: float | None = None, log=print) -> list[dict]:
    """The training set: ICLR submissions of venues (default the latest YEARS decided
    years) found by the queries, each with title, abstract, ratings and mean rating on the
    family's 1 to 10 scale, and decision. Read from the cached JSONL when it exists;
    otherwise built with one forum and one reply search per query and venue, PAUSE seconds
    apart, and cached. budget (seconds) stops issuing new queries once spent. Nothing is
    cached when no row was found."""
    queries = list(queries)
    if not venues and not refresh and (path := cached_dataset(cache)):
        return read_rows(path)
    groups = list(venues or latest_groups(queries[0]))
    if not groups:
        return []
    path = dataset_path(groups, cache)
    if path.exists() and not refresh:
        return read_rows(path)
    start = time.monotonic()
    submissions: dict[str, dict] = {}
    reviews: dict[str, dict] = {}
    decisions: dict[str, str] = {}
    for k, query in enumerate(queries, 1):
        if budget is not None and time.monotonic() - start > budget:
            log(f"budget spent after {k - 1} queries")
            break
        for group in groups:
            lo, hi = anchors.group_scale("ICLR", _year(group))
            for source in ("forum", "reply"):
                found = anchors.fetch(anchors.API, {"term": query, "group": group, "source": source, "limit": limit})
                time.sleep(PAUSE)
                for note in (found or {}).get("notes") or []:
                    if not isinstance(note, dict):
                        continue
                    forum = note.get("forum") or note.get("id")
                    if source == "forum":
                        submissions.setdefault(forum, {
                            "group": group, "venueid": str(anchors._value(note, "venueid") or ""),
                            "title": anchors._plain(str(anchors._value(note, "title") or "")),
                            "abstract": re.sub(r"\s+", " ", str(anchors._value(note, "abstract") or "")).strip()})
                    elif (kind := anchors._kind(note)) == "Official_Review":
                        r = anchors.parse_review(note)
                        if r:
                            on = FAMILY[0] + (r["rating"] - lo) * (FAMILY[1] - FAMILY[0]) / (hi - lo)
                            reviews.setdefault(forum, {})[note.get("id")] = round(on, 3)
                    elif kind == "Decision":
                        decisions[forum] = str(anchors._value(note, "decision") or "")
        log(f"query {k}/{len(queries)} {query!r}: {len(join(submissions, reviews, decisions))} rows "
            f"({time.monotonic() - start:.0f}s)")
    rows = join(submissions, reviews, decisions)
    if rows:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return rows


# ---- features ----------------------------------------------------------------

_BUCKET: dict[str, tuple[int, float]] = {}


def bucket(term: str) -> tuple[int, float]:
    """The term's hashed index and sign; the same on every machine and run."""
    if term not in _BUCKET:
        h = int.from_bytes(hashlib.blake2b(term.encode("utf-8"), digest_size=8).digest(), "little")
        _BUCKET[term] = (h & (DIM - 1), 1.0 if h >> 63 else -1.0)
    return _BUCKET[term]


def terms(text: str) -> list[str]:
    words = TOKEN.findall((text or "").lower())
    return words + [f"{a} {b}" for a, b in zip(words, words[1:])]


class Rows:
    """Sparse rows in CSR form (numpy only)."""

    def __init__(self, indptr, indices, data):
        self.indptr, self.indices, self.data = indptr, indices, data
        self.n = len(indptr) - 1
        self.row = np.repeat(np.arange(self.n), np.diff(indptr))

    def dot(self, w):                           # X @ w
        return np.bincount(self.row, weights=self.data * w[self.indices], minlength=self.n)

    def tdot(self, v):                          # X.T @ v
        return np.bincount(self.indices, weights=self.data * v[self.row], minlength=DIM)

    def take(self, idx):
        idx = np.asarray(idx)
        parts = [np.arange(self.indptr[i], self.indptr[i + 1]) for i in idx]
        sel = np.concatenate(parts) if parts else np.array([], dtype=int)
        indptr = np.concatenate([[0], np.cumsum([len(p) for p in parts])]).astype(np.int64)
        return Rows(indptr, self.indices[sel], self.data[sel])


def features(texts: list[str]) -> Rows:
    """Hashed unigrams and bigrams, sublinear tf (1 + log tf) with a sign per bucket, l2-normalised."""
    indptr, indices, data = [0], [], []
    for text in texts:
        counts: dict[str, int] = {}
        for t in terms(text):
            counts[t] = counts.get(t, 0) + 1
        row: dict[int, float] = {}
        for t, c in counts.items():
            i, s = bucket(t)
            row[i] = row.get(i, 0.0) + s * (1 + math.log(c))
        norm = math.sqrt(sum(v * v for v in row.values())) or 1.0
        for i in sorted(row):
            indices.append(i)
            data.append(row[i] / norm)
        indptr.append(len(indices))
    return Rows(np.array(indptr, dtype=np.int64), np.array(indices, dtype=np.int64), np.array(data, dtype=np.float64))


def text_of(title: str, abstract: str) -> str:
    return f"{title or ''}. {abstract or ''}"


# ---- models ------------------------------------------------------------------

def _cg(apply, b, x0=None, iters: int = 200, tol: float = 1e-6):
    x = np.zeros_like(b) if x0 is None else x0.copy()
    r = b - apply(x)
    p = r.copy()
    rr = r @ r
    stop = tol * tol * max(b @ b, 1e-30)
    for _ in range(iters):
        if rr <= stop:
            break
        ap = apply(p)
        alpha = rr / (p @ ap)
        x += alpha * p
        r -= alpha * ap
        new = r @ r
        p = r + (new / rr) * p
        rr = new
    return x


def fit_ridge(X: Rows, y, lam: float) -> np.ndarray:
    """min ||Xw + b - y||^2 + lam ||w||^2 by conjugate gradient on the normal equations;
    returns [w, b] with the intercept unpenalised."""
    def apply(z):
        w, b = z[:-1], z[-1]
        u = X.dot(w) + b
        return np.append(X.tdot(u) + lam * w, u.sum())
    return _cg(apply, np.append(X.tdot(y), y.sum()), iters=300)


def fit_logistic(X: Rows, y, lam: float, newton: int = 15) -> np.ndarray:
    """L2-regularised logistic regression by Newton's method with CG inner solves; returns [w, b]."""
    z = np.zeros(DIM + 1)
    z[-1] = math.log((y.mean() + 1e-6) / (1 - y.mean() + 1e-6))
    for _ in range(newton):
        p = 1 / (1 + np.exp(-np.clip(X.dot(z[:-1]) + z[-1], -30, 30)))
        g = np.append(X.tdot(p - y) + lam * z[:-1], (p - y).sum())
        d = p * (1 - p) + 1e-9

        def hess(v):
            u = d * (X.dot(v[:-1]) + v[-1])
            return np.append(X.tdot(u) + lam * v[:-1], u.sum())
        step = _cg(hess, g, iters=60, tol=1e-4)
        z -= step
        if np.abs(step).max() < 1e-5:
            break
    return z


def linear(X: Rows, z) -> np.ndarray:
    return X.dot(z[:-1]) + z[-1]


def sigmoid(x):
    return 1 / (1 + np.exp(-np.clip(x, -30, 30)))


def pearson(a, b) -> float:
    a, b = np.asarray(a, float), np.asarray(b, float)
    if a.std() == 0 or b.std() == 0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def auc(scores, labels) -> float:
    """Area under the ROC curve (Mann-Whitney, ties counted half)."""
    scores, labels = np.asarray(scores, float), np.asarray(labels, int)
    pos, neg = labels.sum(), len(labels) - labels.sum()
    if not pos or not neg:
        return 0.5
    order = np.argsort(scores, kind="mergesort")
    ranks = np.empty(len(scores))
    s = scores[order]
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and s[j + 1] == s[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2 + 1
        i = j + 1
    return float((ranks[labels == 1].sum() - pos * (pos + 1) / 2) / (pos * neg))


RIDGE = (1.0, 3.0, 10.0, 30.0, 100.0)
LOGIT = (0.03, 0.1, 0.3, 1.0, 3.0)


def _pick(fit, score, X: Rows, y, grid, seed: int = 1):
    """The penalty from grid that scores best on a 15% validation slice of the training rows."""
    idx = np.random.default_rng(seed).permutation(X.n)
    cut = max(1, int(round(X.n * HOLDOUT)))
    Xv, Xt = X.take(idx[:cut]), X.take(idx[cut:])
    best = max(grid, key=lambda lam: score(linear(Xv, fit(Xt, y[idx[cut:]], lam)), y[idx[:cut]]))
    return best


def train(rows: list[dict], cache: Path | None = None, save: bool = True, seed: int = 0, groups=None) -> dict:
    """Fit the rating and accept models on rows (title, abstract, rating, decision): the
    penalties are chosen on a validation slice of the training part, metrics come from a
    15% held-out part, and the saved weights are refitted on all rows. Returns the meta."""
    rows = [r for r in rows if r.get("rating") is not None and r.get("decision") in ("accept", "reject")]
    if len(rows) < 20:
        raise ValueError(f"too few rows to train on: {len(rows)}")
    X = features([text_of(r["title"], r["abstract"]) for r in rows])
    y = np.array([float(r["rating"]) for r in rows])
    a = np.array([1.0 if r["decision"] == "accept" else 0.0 for r in rows])
    idx = np.random.default_rng(seed).permutation(len(rows))
    cut = max(1, int(round(len(rows) * HOLDOUT)))
    test, tr = idx[:cut], idx[cut:]
    Xte, Xtr = X.take(test), X.take(tr)
    lam_r = _pick(fit_ridge, lambda p, t: -np.abs(p - t).mean() + pearson(p, t), Xtr, y[tr], RIDGE)
    lam_a = _pick(fit_logistic, lambda p, t: auc(p, t), Xtr, a[tr], LOGIT)
    pred = linear(Xte, fit_ridge(Xtr, y[tr], lam_r))
    prob = sigmoid(linear(Xte, fit_logistic(Xtr, a[tr], lam_a)))
    mean = y[tr].mean()
    metrics = {"holdout": int(len(test)), "rating_r": round(pearson(pred, y[test]), 3),
               "rating_mae": round(float(np.abs(pred - y[test]).mean()), 3),
               "baseline_mae": round(float(np.abs(mean - y[test]).mean()), 3), "baseline_r": 0.0,
               "accept_auc": round(auc(prob, a[test]), 3), "baseline_auc": 0.5,
               "accept_rate": round(float(a.mean()), 3)}
    groups = sorted({r.get("group") for r in rows if r.get("group")}) if groups is None else groups
    years = sorted({_year(g) for g in groups}) if groups else []
    meta = {"model": "hashed unigram+bigram text regression (ridge for rating, logistic for accept)",
            "reads": "title and abstract", "dims": DIM, "features": "signed hashing of unigrams and bigrams, "
            "sublinear tf, l2-normalised", "rows": len(rows), "groups": groups,
            "trained_on": f"{len(rows)} ICLR submissions" + (f" ({years[0]}–{years[-1]})" if years else ""),
            "scale": list(FAMILY), "lambda_rating": lam_r, "lambda_accept": lam_a, "metrics": metrics,
            "date": datetime.now().strftime("%Y-%m-%d"), "refit_on_all": True}
    meta["w_rating"], meta["w_accept"] = fit_ridge(X, y, lam_r), fit_logistic(X, a, lam_a)
    if save:
        meta = save_model(meta, cache)
    return meta


def model_stem(meta: dict, cache: Path | None = None) -> Path:
    years = sorted({_year(g) for g in meta.get("groups") or []})
    tag = f"{years[0]}-{years[-1]}" if years else "custom"
    return Path(cache or CACHE) / f"model-{tag}"


def save_model(meta: dict, cache: Path | None = None) -> dict:
    stem = model_stem(meta, cache)
    stem.parent.mkdir(parents=True, exist_ok=True)
    np.save(f"{stem}.rating.npy", np.asarray(meta["w_rating"], dtype=np.float32))
    np.save(f"{stem}.accept.npy", np.asarray(meta["w_accept"], dtype=np.float32))
    info = {k: v for k, v in meta.items() if not k.startswith("w_")}
    Path(f"{stem}.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def load(cache: Path | None = None) -> dict | None:
    """The newest saved model, or None."""
    found = sorted(Path(cache or CACHE).glob("model-*.json"))
    if not found:
        return None
    stem = str(found[-1])[:-len(".json")]
    meta = json.loads(found[-1].read_text(encoding="utf-8"))
    meta["w_rating"] = np.load(f"{stem}.rating.npy").astype(np.float64)
    meta["w_accept"] = np.load(f"{stem}.accept.npy").astype(np.float64)
    return meta


def score(meta: dict, title: str, abstract: str) -> dict:
    X = features([text_of(title, abstract)])
    lo, hi = meta.get("scale") or FAMILY
    rating = float(np.clip(linear(X, meta["w_rating"])[0], lo, hi))
    return {"rating": round(rating, 2), "accept_probability": round(float(sigmoid(linear(X, meta["w_accept"]))[0]), 3),
            "scale": [lo, hi], "metrics": meta["metrics"], "trained_on": meta["trained_on"], "date": meta["date"],
            "rows": meta["rows"], "reads": meta.get("reads", "title and abstract")}


def predict(title: str, abstract: str, allow_train: bool = False, cache: Path | None = None,
            log=lambda *_: None) -> dict | None:
    """The predicted mean rating (1 to 10) and accept probability of a submission from its
    title and abstract, with the model's held-out metrics. Without a saved model, trains
    one when allow_train (collecting the dataset if it is not cached), else None. None
    too when too few rows could be collected."""
    meta = load(cache)
    if meta is None:
        if not allow_train:
            return None
        rows = collect(cache=cache, log=log)
        if len(rows) < MIN_ROWS:
            return None
        meta = train(rows, cache=cache)
    return score(meta, title, abstract)


if __name__ == "__main__":       # python -m auto_research.tasks.review.predictor [budget seconds]
    import sys
    t0 = time.monotonic()
    data = collect(budget=float(sys.argv[1]) if len(sys.argv) > 1 else None)
    print(f"{len(data)} rows, {time.monotonic() - t0:.0f}s")
    m = train(data)
    print(json.dumps({k: v for k, v in m.items() if not k.startswith("w_")}, ensure_ascii=False, indent=2))

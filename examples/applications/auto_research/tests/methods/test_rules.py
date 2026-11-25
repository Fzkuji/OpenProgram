"""The AST rules, each on a leaky snippet and its clean version."""

import pytest

from auto_research.tasks.methods.rules import RULES, lint

from .repo import CLEAN, DATA, LEAKY

CASES = {
    "preprocess-before-split": (
        "from sklearn.decomposition import PCA\nfrom sklearn.model_selection import KFold\n"
        "pca = PCA(5)\nZ = pca.fit_transform(X)\nkf = KFold(5)\nfor a, b in kf.split(Z):\n    pass\n",
        "from sklearn.decomposition import PCA\nfrom sklearn.model_selection import KFold\n"
        "kf = KFold(5)\nfor a, b in kf.split(X):\n    pca = PCA(5)\n    Z = pca.fit_transform(X[a])\n"),
    "selection-before-split": (
        "scores = mutual_info_classif(X, y)\nX_train, X_test = train_test_split(X, random_state=0)\n",
        "X_train, X_test = train_test_split(X, random_state=0)\nscores = mutual_info_classif(X_train, y)\n"),
    "test-in-tuning": (
        "best = 1e9\nfor epoch in range(100):\n    fit_epoch(model, train_loader)\n"
        "    loss = evaluate(model, test_loader)\n    if loss < best:\n        best = loss\n",
        "best = 1e9\nfor epoch in range(100):\n    fit_epoch(model, train_loader)\n"
        "    loss = evaluate(model, val_loader)\n    if loss < best:\n        best = loss\n"
        "final = evaluate(model, test_loader)\n"),
    "target-leakage": (
        'features = ["age", "income", "target"]\n',
        'features = ["age", "income"]\n'),
    "no-seed": (
        "import torch\nx = torch.randn(3)\n",
        "import torch\ntorch.manual_seed(0)\nx = torch.randn(3)\n"),
    "shuffled-cv-time-series": (
        'df = pd.read_csv("a.csv", parse_dates=["date"])\nkf = KFold(n_splits=5, shuffle=True, random_state=0)\n',
        'df = pd.read_csv("a.csv", parse_dates=["date"])\nkf = TimeSeriesSplit(n_splits=5)\n'),
    "evaluate-on-train": (
        "model.fit(X_train, y_train)\nprint(accuracy_score(y_train, model.predict(X_train)))\n",
        "model.fit(X_train, y_train)\nprint(accuracy_score(y_train, model.predict(X_train)))\n"
        "print(accuracy_score(y_test, model.predict(X_test)))\n"),
    "metric-same-split": (
        "model.fit(X, y)\npred = model.predict(X)\nprint(f1_score(y, pred))\n",
        "model.fit(X_a, y_a)\npred = model.predict(X_b)\nprint(f1_score(y_b, pred))\n"),
    "duplicates-unchecked": (
        "a, b = train_test_split(df, random_state=0)\n",
        "df = df.drop_duplicates()\na, b = train_test_split(df, random_state=0)\n"),
}


@pytest.mark.parametrize("rule", sorted(CASES))
def test_each_rule_fires_on_the_leak_and_not_on_the_fix(rule):
    leaky, clean = CASES[rule]
    found, broken = lint({"x.py": leaky})
    assert not broken and rule in {f["rule"] for f in found}
    hit = next(f for f in found if f["rule"] == rule)
    assert hit["severity"] == RULES[rule]["severity"] and hit["fix"] and hit["line"] >= 1
    assert rule not in {f["rule"] for f in lint({"x.py": clean})[0]}


def test_every_rule_has_a_case_and_a_severity():
    assert set(CASES) == set(RULES)
    assert {r["severity"] for r in RULES.values()} == {"high", "medium", "low"}


def test_the_leaky_script_and_the_clean_one():
    found, _ = lint({"train.py": LEAKY, "data.py": DATA})
    rules = {(f["rule"], f["line"]) for f in found}
    assert ("preprocess-before-split", 13) in rules and ("selection-before-split", 14) in rules
    assert ("target-leakage", 11) in rules and ("test-in-tuning", 19) in rules
    assert ("metric-same-split", 24) in rules and ("no-seed", 15) in rules
    assert ("shuffled-cv-time-series", 15) in rules and ("duplicates-unchecked", 15) in rules
    assert "evaluate-on-train" not in {r for r, _ in rules}
    assert lint({"train.py": CLEAN, "data.py": DATA})[0] == []


def test_a_file_that_does_not_parse_is_reported():
    found, broken = lint({"bad.py": "def (:\n"})
    assert found == [] and "bad.py" in broken

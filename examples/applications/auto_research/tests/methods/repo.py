"""Experiment repositories for the methods tests: one with the usual leakage
patterns, one that does the same experiment cleanly."""

import subprocess

SPEC = """# Experiment loop
Approved: yes
Edit: train.py
Command: python train.py --out run
Metric: accuracy
Direction: higher
Budget: 5 min
Stop: 30 runs
"""
DATA = """import pandas as pd


def load():
    return pd.read_csv("data.csv", parse_dates=["date"])
"""
LEAKY = """import pandas as pd
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
from sklearn.feature_selection import SelectKBest
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score
from data import load

df = load()
y = df["label"]
X = df.drop(columns=["id"])
scaler = StandardScaler()
X = scaler.fit_transform(X)
X = SelectKBest(k=5).fit_transform(X, y)
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2)
best, best_c = 0, None
for c in [0.1, 1.0, 10.0]:
    clf = LogisticRegression(C=c).fit(X_train, y_train)
    acc = accuracy_score(y_test, clf.predict(X_test))
    if acc > best:
        best, best_c = acc, c
model = LogisticRegression(C=best_c)
model.fit(X, y)
print("accuracy:", accuracy_score(y, model.predict(X)))
"""
CLEAN = """import random

import numpy as np
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score
from data import load

random.seed(0)
np.random.seed(0)
df = load().sort_values("date").drop_duplicates()
y = df["label"]
X = df.drop(columns=["label", "id", "date"])
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, shuffle=False)
X_tr, X_val, y_tr, y_val = train_test_split(X_train, y_train, test_size=0.25, shuffle=False)
scaler = StandardScaler().fit(X_tr)
best, best_c = 0, None
for c in [0.1, 1.0, 10.0]:
    clf = LogisticRegression(C=c).fit(scaler.transform(X_tr), y_tr)
    acc = accuracy_score(y_val, clf.predict(scaler.transform(X_val)))
    if acc > best:
        best, best_c = acc, c
model = LogisticRegression(C=best_c)
model.fit(scaler.transform(X_train), y_train)
print("accuracy:", accuracy_score(y_test, model.predict(scaler.transform(X_test))))
"""


def make(tmp_path, train=LEAKY, name="paper"):
    repo = tmp_path / name
    (repo / "workflow").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "train.py").write_text(train)
    (repo / "data.py").write_text(DATA)
    (repo / "workflow" / "experiment.md").write_text(SPEC)
    return repo

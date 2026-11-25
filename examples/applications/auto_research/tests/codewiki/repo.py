"""A paper repository with its code inside: a method with two subsections, a labelled
equation, an algorithm and a data-collection subsection the code does not implement."""

import subprocess
from pathlib import Path

MAIN = r"""\documentclass{article}
\title{Gated Attention for Tiny Models}
\begin{document}
\maketitle
\begin{abstract}
We gate attention scores and train with a margin loss.
\end{abstract}
\section{Introduction}
Attention is everywhere and we make it cheaper for small models.
\section{Method}\label{sec:method}
Our model stacks gated attention blocks.
\subsection{Gated Attention}
Each block scores queries against keys and gates the scores with a sigmoid before the softmax.
\begin{equation}
a = \mathrm{softmax}(\sigma(g) \cdot q k^\top) \label{eq:gate}
\end{equation}
The gate $g$ is learned per head.
\subsection{Margin Loss}
We train with a hinge loss with margin one on the logits of the correct class.
\subsection{Data Collection}
We collected ten thousand sentences from public forums and filtered them by hand.
\begin{algorithm}
\caption{Training loop}\label{alg:train}
\begin{algorithmic}
\FOR{each batch}
\STATE compute the margin loss and take an SGD step
\ENDFOR
\end{algorithmic}
\end{algorithm}
\section{Experiments}
The model reaches 91.2 accuracy.
\begin{equation}
\mathrm{acc} = \frac{\#\mathrm{correct}}{N}
\end{equation}
\end{document}
"""
MODEL = '''"""Gated attention and the block that stacks it."""
import math

from .loss import margin_loss


class GatedAttention:
    """Attention whose scores are gated by a learned sigmoid."""

    def __init__(self, heads):
        self.gate = [0.0] * heads

    def forward(self, q, k):
        scores = [qi * ki for qi, ki in zip(q, k)]
        gated = [s / (1 + math.exp(-self.gate[0])) for s in scores]
        return softmax(gated)


class Block:
    def __init__(self, heads):
        self.attn = GatedAttention(heads)

    def forward(self, q, k):
        return self.attn(q, k)


def softmax(xs):
    m = max(xs)
    e = [math.exp(x - m) for x in xs]
    return [x / sum(e) for x in e]


def train_step(block, q, k, label):
    out = block.forward(q, k)
    return margin_loss(out, label)
'''
LOSS = '''"""The margin loss."""


def margin_loss(logits, label, margin=1.0):
    """Hinge loss on the correct class."""
    return sum(max(0.0, margin - logits[label] + x) for i, x in enumerate(logits) if i != label)
'''
TRAIN = '''import argparse

from gated.model import Block, train_step

p = argparse.ArgumentParser()
p.add_argument("--heads", type=int, default=2)
args = p.parse_args()


def main():
    block = Block(args.heads)
    for step in range(10):
        loss = train_step(block, [1.0, 2.0], [0.5, 0.1], 0)
        print(loss)


if __name__ == "__main__":
    main()
'''
TS = """export function tokenize(text: string): string[] {
  return text.split(" ");
}

export class Viewer {
  render() { return tokenize("a b"); }
}

const helper = (x: number) => x + 1;
"""
GO = """package main

type Server struct {}

func (s *Server) Handle() {}

func main() {
\tHandle()
}
"""
RUST = """pub struct Cache {}

pub fn lookup(key: &str) -> Option<u32> { None }
"""
PYPROJECT = """[project]
name = "gated"
version = "0.1"

[project.scripts]
gated-train = "train:main"
"""


def make(tmp: Path) -> Path:
    paper = tmp / "paper"
    (paper / "gated").mkdir(parents=True)
    (paper / "tests").mkdir()
    (paper / "web").mkdir()
    (paper / "drafts" / "old").mkdir(parents=True)
    (paper / "main.tex").write_text(MAIN)
    (paper / "gated" / "__init__.py").write_text("")
    (paper / "gated" / "model.py").write_text(MODEL)
    (paper / "gated" / "loss.py").write_text(LOSS)
    (paper / "train.py").write_text(TRAIN)
    (paper / "tests" / "test_model.py").write_text("from gated.model import softmax\n\n\ndef test_softmax():\n    assert softmax([0.0])\n")
    (paper / "web" / "app.ts").write_text(TS)
    (paper / "web" / "server.go").write_text(GO)
    (paper / "web" / "cache.rs").write_text(RUST)
    (paper / "drafts" / "old" / "junk.py").write_text("def junk():\n    pass\n")
    (paper / "config.yaml").write_text("heads: 2\nlr: 0.1\n")
    (paper / "pyproject.toml").write_text(PYPROJECT)
    subprocess.run(["git", "init", "-q", str(paper)], check=True)
    return paper

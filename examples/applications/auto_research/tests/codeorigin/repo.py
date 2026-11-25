"""A small released-code repository for the code_origin tests: an MIT project with a
file copied verbatim from elsewhere, a GPL file, a vendored BSD library, an own file
that pastes part of the library, and a fake Software Heritage archive."""

import subprocess

MIT = """MIT License

Copyright (c) 2026 Probe Authors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction.
"""
UPSTREAM_MIT = MIT.replace("2026 Probe Authors", "2020 Andrej Karpathy")
BSD3 = """Copyright (c) 2020 Fastlib Developers
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:
3. Neither the name of the copyright holder nor the names of its contributors
   may be used to endorse or promote products derived from this software.
"""
GPT = '''"""
Full definition of a GPT Language Model, all of it in this single file.
"""
import math

import torch
import torch.nn as nn
from torch.nn import functional as F


class NewGELU(nn.Module):
    def forward(self, x):
        return 0.5 * x * (1.0 + torch.tanh(math.sqrt(2.0 / math.pi) * (x + 0.044715 * torch.pow(x, 3.0))))


class CausalSelfAttention(nn.Module):
    def __init__(self, config):
        super().__init__()
        assert config.n_embd % config.n_head == 0
        self.c_attn = nn.Linear(config.n_embd, 3 * config.n_embd)
        self.c_proj = nn.Linear(config.n_embd, config.n_embd)
        self.attn_dropout = nn.Dropout(config.attn_pdrop)
        self.n_head = config.n_head

    def forward(self, x):
        B, T, C = x.size()
        q, k, v = self.c_attn(x).split(C, dim=2)
        att = (q @ k.transpose(-2, -1)) * (1.0 / math.sqrt(k.size(-1)))
        att = F.softmax(att, dim=-1)
        return self.c_proj(self.attn_dropout(att) @ v)
'''
GPL = '''# (c) 2012-2014, Michael DeHaan <michael.dehaan@gmail.com>
#
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
import re
import sys

ANSIBLE_COLOR = True


def parsecolor(color):
    """SGR parameter string for the specified color name."""
    matches = re.match(r"color(?P<color>[0-9]+)|(?P<rgb>rgb(?P<red>[0-5])(?P<green>[0-5])(?P<blue>[0-5]))", color)
    if not matches:
        return "39"
    if matches.group("color"):
        return u"38;5;%d" % int(matches.group("color"))
    return u"38;5;%d" % (16 + 36 * int(matches.group("red")) + 6 * int(matches.group("green")))
'''
OPS = '''# Copyright (c) 2020 Fastlib Developers
# Licensed under the BSD 3-Clause license; see LICENSE.
import numpy as np


def softmax(x, axis=-1):
    shifted = x - np.max(x, axis=axis, keepdims=True)
    exps = np.exp(shifted)
    return exps / np.sum(exps, axis=axis, keepdims=True)


def layer_norm(x, gamma, beta, eps=1e-5):
    mean = np.mean(x, axis=-1, keepdims=True)
    var = np.var(x, axis=-1, keepdims=True)
    return gamma * (x - mean) / np.sqrt(var + eps) + beta


def gelu(x):
    return 0.5 * x * (1.0 + np.tanh(np.sqrt(2.0 / np.pi) * (x + 0.044715 * np.power(x, 3))))
'''
TRAIN = OPS.split("import numpy as np\n", 1)[1].replace("def gelu", "def my_gelu") + '''

def train(data, steps=10):
    """Our own training loop."""
    losses = []
    for step in range(steps):
        losses.append(float(np.mean(softmax(data))))
    return losses
'''
TRAINER = '''"""
Simple training loop; Boilerplate that could apply to any arbitrary neural network.
"""
import time
from collections import defaultdict

import torch
from torch.utils.data.dataloader import DataLoader


class Trainer:
    def __init__(self, config, model, train_dataset):
        self.config = config
        self.model = model
        self.optimizer = None
        self.train_dataset = train_dataset
        self.callbacks = defaultdict(list)
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model = self.model.to(self.device)
        self.iter_num = 0
        self.iter_time = 0.0
        self.iter_dt = 0.0

    def run(self):
        model, config = self.model, self.config
        self.optimizer = model.configure_optimizers(config)
        train_loader = DataLoader(self.train_dataset, shuffle=False, pin_memory=True,
                                  batch_size=config.batch_size, num_workers=config.num_workers)
        model.train()
        self.iter_time = time.time()
        data_iter = iter(train_loader)
        while True:
            batch = [t.to(self.device) for t in next(data_iter)]
            x, y = batch
            logits, self.loss = model(x, y)
            model.zero_grad(set_to_none=True)
            self.loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), config.grad_norm_clip)
            self.optimizer.step()
            self.iter_num += 1
'''
MY_TRAINER = TRAINER.replace("class Trainer:", "class ProbeTrainer:").replace(
    "Simple training loop; Boilerplate that could apply to any arbitrary neural network.", "Training loop for the probe.")
README = "# Probe\n\nTrain the probe with `python train.py`.\n"


def make(tmp_path, gpl=True, copied=True, name="paper"):
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    files = {"LICENSE": MIT, "README.md": README, "third_party/fastlib/LICENSE": BSD3,
             "third_party/fastlib/ops.py": OPS, "train.py": "import numpy as np\n" + TRAIN, "pkg/__init__.py": "",
             "drafts/old.py": GPT, "workflow/claims.md": "# Claims\n"}
    if copied:
        files["models/gpt.py"] = GPT
        files["models/trainer.py"] = MY_TRAINER
    if gpl:
        files["utils/color.py"] = GPL
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    (repo / "weights.bin").write_bytes(b"\0\1\2" * 50)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    return repo


class FakeArchive:
    """Software Heritage as the tests need it: a set of known blobs and origins holding
    {path: text} at their HEAD. problem set means the archive cannot be asked."""

    def __init__(self, known=(), origins=None, problem=""):
        from auto_research.tasks.codeorigin.files import sha1_git
        self.sha = lambda text: sha1_git(text.encode())
        self.known_set = set(known)
        self.origins = origins or {}
        self.problem = problem
        self.calls = 0
        self.blobs = {self.sha(t): t for files in self.origins.values() for t in files.values()}
        self.asked = []

    def known(self, swhids):
        self.calls += 1
        if self.problem:
            return None
        self.asked += swhids
        return {s: s.split(":")[-1] in self.known_set for s in swhids}

    def revision(self, origin):
        self.calls += 1
        return f"rev:{origin}" if origin in self.origins else ""

    def lookup(self, rev, path):
        self.calls += 1
        text = self.origins.get(rev[4:], {}).get(path)
        return {"sha1_git": self.sha(text), "length": len(text)} if text is not None else None

    def root(self, rev):
        return [{"name": p, "type": "file", "target": self.sha(t)} for p, t in self.origins.get(rev[4:], {}).items()
                if "/" not in p]

    def raw(self, sha):
        return self.blobs[sha].encode() if sha in self.blobs else None

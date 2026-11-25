"""image_reuse: the hashes on hand-made grids, the matching and caption logic with a fake
image reader, and (when Pillow is importable) real generated images."""

import json

import pytest

from auto_research.tasks.integrity import image_reuse, images

from .conftest import make_repo

MAIN = r"""\documentclass{article}
\usepackage{graphicx}
\graphicspath{{figures/}}
\begin{document}
\begin{figure}
\includegraphics{arch}
\caption{Our architecture.}
\end{figure}
\begin{figure}
\includegraphics{curve.png}
\caption{Learning curve, adapted from \cite{old2023}.}
\end{figure}
\begin{figure}
\includegraphics{panel_a}
\includegraphics{panel_b}
\caption{Two runs.}
\end{figure}
\end{document}
"""


def pattern(seed: int, w: int = 9) -> list[list[float]]:
    return [[float((r * 37 + c * 91 + seed * 53) % 256) for c in range(w)] for r in range(8)]


def test_hashes_on_grids():
    g = pattern(1, 8)
    assert images.ahash(g) == images.ahash([[v + 1 for v in row] for row in g])
    assert images.dhash(pattern(1)) != images.dhash(pattern(2))
    assert images.flat([[128.0] * 8 for _ in range(8)])
    a = {"ahash": f"{images.ahash(g):016x}", "dhash": f"{images.dhash(pattern(1)):016x}", "flat": False}
    assert images.distance(a, dict(a)) == (0, 0)
    assert images.distance(a, {**a, "flat": True}) is None


@pytest.fixture
def paper(tmp_path):
    files = {f"figures/{n}.png": b"x" for n in ("arch", "curve", "panel_a", "panel_b")}
    files.update({"old/fig1.png": b"x", "old/fig2.png": b"x", "old/other.png": b"x"})
    return make_repo(tmp_path, MAIN, files)


def fake(monkeypatch, groups: dict[str, int]):
    """Images named in groups hash to the pattern of their group number."""
    def hashes(path):
        seed = groups.get(path.name)
        if seed is None:
            return None
        small, wide = [row[:8] for row in pattern(seed)], pattern(seed)
        return {"ahash": f"{images.ahash(small):016x}", "dhash": f"{images.dhash(wide):016x}", "flat": False}

    monkeypatch.setattr(images, "_pil", lambda: object())
    monkeypatch.setattr(images, "hashes", hashes)


def test_uncredited_reuse_fails_credited_reuse_and_duplicates_are_notes(paper, monkeypatch):
    fake(monkeypatch, {"arch.png": 1, "fig1.png": 1, "curve.png": 2, "fig2.png": 2,
                       "panel_a.png": 3, "panel_b.png": 3, "other.png": 4})
    result = json.loads(image_reuse(str(paper), own_papers="old"))
    assert result["status"] == "FAIL" and result["supported"]
    data = json.loads(open(result["findings"], encoding="utf-8").read())
    kinds = {(f["kind"], f["figure"]) for f in data["findings"]}
    assert ("reused_figure", "figures/arch.png") in kinds
    assert ("credited_reuse", "figures/curve.png") in kinds
    assert ("duplicate_figure", "figures/panel_a.png") in kinds
    reused = next(f for f in data["findings"] if f["kind"] == "reused_figure")
    assert reused["other"].endswith("fig1.png") and reused["where_tex"] == "main.tex:5"


def test_without_own_papers_only_duplicates_count(paper, monkeypatch):
    fake(monkeypatch, {"arch.png": 1, "curve.png": 2, "panel_a.png": 3, "panel_b.png": 4})
    result = json.loads(image_reuse(str(paper)))
    assert result["status"] == "PASS" and result["matches"] == 0
    with pytest.raises(ValueError):
        image_reuse(str(paper), own_papers="nowhere")


def test_without_pillow_the_check_is_unsupported(paper, monkeypatch):
    monkeypatch.setattr(images, "_pil", lambda: None)
    result = json.loads(image_reuse(str(paper), own_papers="old"))
    assert result["status"] == "PASS_WITH_NOTES" and result["supported"] is False


def test_credit_phrases():
    for caption in ("Reproduced from Smith et al.", "Adapted from \\cite{x}", "Results from~\\citet{x}.",
                    "图引自文献[3]", "改编自我们的旧工作"):
        assert images.CREDIT.search(caption)
    assert not images.CREDIT.search("Results from our new run.")


def test_real_images_with_pillow(paper):
    PIL = pytest.importorskip("PIL.Image")
    import random
    rng = random.Random(0)
    base = PIL.new("L", (64, 64))
    base.putdata([rng.randrange(256) for _ in range(64 * 64)])
    base = base.resize((256, 256))
    base.save(paper / "figures" / "arch.png")
    base.resize((200, 200)).save(paper / "old" / "fig1.png")     # rescaled copy
    other = PIL.new("L", (64, 64))
    other.putdata([rng.randrange(256) for _ in range(64 * 64)])
    for name in ("curve", "panel_a", "panel_b"):
        other.rotate({"curve": 90, "panel_a": 180, "panel_b": 270}[name]).save(paper / "figures" / f"{name}.png")
    for name in ("fig2", "other"):
        PIL.new("L", (32, 32), 255).save(paper / "old" / f"{name}.png")
    result = json.loads(image_reuse(str(paper), own_papers="old"))
    assert result["status"] == "FAIL"
    data = json.loads(open(result["findings"], encoding="utf-8").read())
    assert [f["figure"] for f in data["findings"] if f["kind"] == "reused_figure"] == ["figures/arch.png"]

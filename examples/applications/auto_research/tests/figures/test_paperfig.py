"""The helper module copied into a paper: it draws and saves with the publication style."""

import importlib.util
import sys

import pytest

from auto_research import stages
from auto_research.tasks.figures.workflow import can_plot

pytestmark = pytest.mark.skipif(not can_plot(sys.executable), reason="this interpreter cannot import matplotlib")


@pytest.fixture
def paperfig(monkeypatch):
    monkeypatch.setenv("MPLBACKEND", "Agg")
    spec = importlib.util.spec_from_file_location("paperfig", stages.TEMPLATES / "paperfig.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_helper_draws_and_the_figure_is_saved_in_each_format(paperfig, tmp_path):
    import matplotlib.pyplot as plt
    paperfig.apply_publication_style(paperfig.FigureStyle(font_size=24, axes_linewidth=3))
    assert plt.rcParams["axes.spines.top"] is False and plt.rcParams["font.size"] == 24
    fig, axes = paperfig.create_subplots(2, 3, figsize=(18, 8))
    assert axes.shape == (6,)
    bars = paperfig.make_grouped_bar(axes[0], ["A", "B"], [[1, 2], [2, 1]], ["Ours", "Base"], annotate=True)
    assert len(bars) == 2 and len(axes[0].patches) == 4
    paperfig.make_trend(axes[1], [0, 1, 2], [[1, 2, 3], [[1, 2, 3], [3, 2, 1]]], ["curve", "runs"])
    assert len(axes[1].lines) == 2 and len(axes[1].collections) == 1      # a band only for repeated runs
    paperfig.make_heatmap(axes[2], [[0, 1], [1, 0]], x_labels=["a", "b"], y_labels=["a", "b"], annotate=True)
    paperfig.make_scatter(axes[3], [1, 2], [2, 1], label="points")
    paperfig.make_sphere_illustration(axes[4], resolution=32)
    saved = paperfig.finalize_figure(fig, tmp_path / "out" / "result", formats=["png", "pdf"])
    assert [p.name for p in saved] == ["result.png", "result.pdf"] and all(p.stat().st_size for p in saved)
    assert not plt.fignum_exists(fig.number)


def test_bad_input_is_rejected(paperfig, tmp_path):
    fig, axes = paperfig.create_subplots()
    with pytest.raises(ValueError):
        paperfig.make_grouped_bar(axes[0], ["A", "B"], [[1, 2, 3]], ["x"])
    with pytest.raises(ValueError):
        paperfig.make_trend(axes[0], [0, 1], [[1, 2, 3]], ["x"])
    with pytest.raises(ValueError):
        paperfig.finalize_figure(fig, tmp_path / "a", formats=["bmp"])
    assert [p.suffix for p in paperfig.finalize_figure(fig, tmp_path / "b.png")] == [".png"]

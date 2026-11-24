"""Publication style and plot helpers for this paper's figures.

A plotting script in this folder starts with:

    from paperfig import PALETTE, FigureStyle, apply_publication_style, finalize_figure

The palette, the style values and the function signatures follow the
scientific-figure-making skill of figures4papers by Chen Liu
(https://github.com/ChenLiu-1996/figures4papers, references/api.md). One difference:
make_trend draws a band only from repeated runs, never from a single curve.
"""

from dataclasses import dataclass
from pathlib import Path

import matplotlib.colors
import matplotlib.pyplot as plt
import numpy as np

PALETTE = {
    "blue_main": "#0F4D92",
    "blue_secondary": "#3775BA",
    "green_1": "#DDF3DE", "green_2": "#AADCA9", "green_3": "#8BCF8B",
    "red_1": "#F6CFCB", "red_2": "#E9A6A1", "red_strong": "#B64342",
    "neutral": "#CFCECE", "highlight": "#FFD700",
    "teal": "#42949E", "violet": "#9A4D8E",
}
# Blues: the proposed method. Greens: improvements. Reds: baselines. Neutral: background.
DEFAULT_COLORS = [PALETTE[k] for k in ("blue_main", "green_3", "red_strong", "teal", "violet", "neutral")]
FORMATS = ("pdf", "svg", "eps", "png", "jpg", "jpeg", "tif", "tiff")


@dataclass(frozen=True)
class FigureStyle:
    font_size: int = 16                 # 24 for large bar panels, 15-16 for compact plots
    axes_linewidth: float = 2.5         # 3 for big bars, 2 for compact plots
    use_tex: bool = False               # True only with LaTeX installed and math-heavy labels
    font_family: tuple[str, ...] = ("DejaVu Sans", "Helvetica", "Arial", "sans-serif")


def apply_publication_style(style: FigureStyle | None = None) -> None:
    """Set the rcParams once, before any figure is created."""
    style = style or FigureStyle()
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": [f for f in style.font_family if f != "sans-serif"],
        "font.size": style.font_size,
        "axes.linewidth": style.axes_linewidth,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "legend.frameon": False,
        "text.usetex": style.use_tex,
        "svg.fonttype": "none",         # text stays editable in vector files
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "savefig.dpi": 300,
    })


def create_subplots(nrows: int = 1, ncols: int = 1, figsize=None, **kwargs):
    """(fig, axes) with axes as a flat array, whatever the grid."""
    fig, axes = plt.subplots(nrows, ncols, figsize=figsize, squeeze=False, **kwargs)
    return fig, axes.ravel()


def finalize_figure(fig, out_path, formats=None, dpi: int = 300, close: bool = True, pad: float = 0.05,
                    **kwargs) -> list[Path]:
    """Save fig once per format and return the paths. Without formats, the extension of
    out_path decides; a path with no extension is saved as pdf, svg and eps."""
    out = Path(out_path)
    suffix = out.suffix[1:].lower()
    base = out.with_suffix("") if suffix in FORMATS else out
    formats = [f.lower().lstrip(".") for f in (formats or ([suffix] if suffix in FORMATS else ["pdf", "svg", "eps"]))]
    unknown = [f for f in formats if f not in FORMATS]
    if unknown:
        raise ValueError(f"unsupported formats {unknown}; use one of {', '.join(FORMATS)}")
    base.parent.mkdir(parents=True, exist_ok=True)
    saved = []
    for fmt in formats:
        path = base.parent / f"{base.name}.{fmt}"
        fig.savefig(path, dpi=dpi, bbox_inches="tight", pad_inches=pad, **kwargs)
        saved.append(path)
    if close:
        plt.close(fig)
    return saved


def _array(values, ndim: int, name: str) -> np.ndarray:
    data = np.asarray(values, dtype=float)
    if data.ndim != ndim:
        raise ValueError(f"{name} must be {ndim}D, not {data.ndim}D")
    return data


def make_grouped_bar(ax, categories, series, labels, ylabel: str = "Value", colors=None, annotate: bool = False):
    """Grouped bars: one group per category, one bar per series. Returns the last BarContainer."""
    data = _array(series, 2, "series")
    if data.shape[1] != len(categories):
        raise ValueError(f"each series needs {len(categories)} values, one per category")
    if len(labels) != data.shape[0]:
        raise ValueError(f"{data.shape[0]} series need {data.shape[0]} labels")
    colors = colors or DEFAULT_COLORS
    width = 0.8 / data.shape[0]
    centers = np.arange(len(categories))
    bars = None
    for i, (values, label) in enumerate(zip(data, labels)):
        bars = ax.bar(centers + (i - (data.shape[0] - 1) / 2) * width, values, width, label=label,
                      color=colors[i % len(colors)], edgecolor="black", linewidth=1.5)
        if annotate:
            annotate_bars(ax, bars)
    ax.set_xticks(centers, categories)
    ax.set_ylabel(ylabel)
    ax.legend()
    return bars


def annotate_bars(ax, bars, fmt: str = "{:.2f}", fontsize: int = 10, padding: int = 3) -> None:
    """Print each bar's value above it."""
    ax.bar_label(bars, labels=[fmt.format(v) for v in bars.datavalues], fontsize=fontsize, padding=padding)


def make_trend(ax, x, y_series, labels, colors=None, ylabel=None, xlabel=None, show_shadow: bool = True) -> None:
    """One line per series. A series is a curve (1D, the length of x) or repeated runs
    (2D, runs by the length of x): runs are drawn as their mean, with a band of one
    standard deviation when show_shadow is set. A single curve gets no band."""
    x = _array(x, 1, "x")
    if len(labels) != len(y_series):
        raise ValueError(f"{len(y_series)} series need {len(y_series)} labels")
    colors = colors or DEFAULT_COLORS
    for i, (values, label) in enumerate(zip(y_series, labels)):
        data = np.asarray(values, dtype=float)
        if data.ndim not in (1, 2) or data.shape[-1] != len(x):
            raise ValueError(f"series {label!r} must have {len(x)} values per curve")
        color = colors[i % len(colors)]
        mean = data if data.ndim == 1 else data.mean(axis=0)
        ax.plot(x, mean, label=label, color=color, linewidth=2.5)
        if data.ndim == 2 and show_shadow:
            spread = data.std(axis=0)
            ax.fill_between(x, mean - spread, mean + spread, color=color, alpha=0.2, linewidth=0)
    if xlabel:
        ax.set_xlabel(xlabel)
    if ylabel:
        ax.set_ylabel(ylabel)
    ax.legend()


def make_heatmap(ax, matrix, x_labels=None, y_labels=None, cmap: str = "magma", cbar_label=None,
                 annotate: bool = False):
    """A 2D heatmap with a colorbar; annotate prints each cell's value."""
    data = _array(matrix, 2, "matrix")
    image = ax.imshow(data, cmap=cmap, aspect="auto")
    if x_labels is not None:
        if len(x_labels) != data.shape[1]:
            raise ValueError(f"x_labels needs {data.shape[1]} names")
        ax.set_xticks(np.arange(data.shape[1]), x_labels)
    if y_labels is not None:
        if len(y_labels) != data.shape[0]:
            raise ValueError(f"y_labels needs {data.shape[0]} names")
        ax.set_yticks(np.arange(data.shape[0]), y_labels)
    bar = ax.figure.colorbar(image, ax=ax)
    if cbar_label:
        bar.set_label(cbar_label)
    if annotate:
        middle = (data.max() + data.min()) / 2
        for (row, col), value in np.ndenumerate(data):
            ax.text(col, row, f"{value:.2f}", ha="center", va="center",
                    color="white" if value < middle else "black")
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)
    return image


def make_scatter(ax, x, y, label=None, color=None, size: float = 50, alpha: float = 0.7):
    """One scatter series."""
    x, y = _array(x, 1, "x"), _array(y, 1, "y")
    if len(x) != len(y):
        raise ValueError("x and y must have the same length")
    points = ax.scatter(x, y, s=size, alpha=alpha, color=color or PALETTE["blue_main"], label=label, linewidths=0)
    if label:
        ax.legend()
    return points


def make_sphere_illustration(ax, light_dir=(-0.5, 0.5, 0.8), resolution: int = 128, alpha: float = 0.6):
    """A shaded disk that reads as a sphere, for concept diagrams."""
    light = np.asarray(light_dir, dtype=float)
    light = light / np.linalg.norm(light)
    grid = np.linspace(-1, 1, resolution)
    xs, ys = np.meshgrid(grid, grid)
    inside = xs ** 2 + ys ** 2 <= 1
    zs = np.sqrt(np.clip(1 - xs ** 2 - ys ** 2, 0, None))
    shade = np.clip(xs * light[0] + ys * light[1] + zs * light[2], 0, 1)
    rgba = np.zeros((resolution, resolution, 4))
    base = np.array(matplotlib.colors.to_rgb(PALETTE["blue_secondary"]))
    rgba[..., :3] = base * (0.35 + 0.65 * shade[..., None]) + 0.25 * shade[..., None] ** 8
    rgba[..., :3] = np.clip(rgba[..., :3], 0, 1)
    rgba[..., 3] = np.where(inside, alpha, 0)
    image = ax.imshow(rgba, extent=(-1, 1, -1, 1), origin="lower")
    ax.set_aspect("equal")
    ax.set_axis_off()
    return image


__all__ = ["DEFAULT_COLORS", "FORMATS", "PALETTE", "FigureStyle", "annotate_bars", "apply_publication_style",
           "create_subplots", "finalize_figure", "make_grouped_bar", "make_heatmap", "make_scatter",
           "make_sphere_illustration", "make_trend"]

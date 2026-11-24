"""Build small Paper objects for the check tests."""

import importlib

prepare = importlib.import_module("auto_research.tasks.revision.steps.prepare")
venue = importlib.import_module("auto_research.tasks.revision.rules.venue")

FIG = r"""\begin{figure}[%s]
\includegraphics{figures/a}
\caption{%s}
\label{fig:%s}
\end{figure}
"""


def doc(*parts: str) -> str:
    return "\\begin{document}\n" + "\n\n".join(parts) + "\n\\bibliography{references}"


def make(draft: str = "", base: str | None = None, **fields):
    """A prepared Paper; the baseline defaults to the draft."""
    return prepare.Paper(draft_flat=draft, base_flat=draft if base is None else base, **fields)


def levels(found: list[dict]) -> list[tuple[str, str]]:
    return [(f["level"], f["check"]) for f in found]

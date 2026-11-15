"""Drive the app's Select (Radix) the way a person does.

The app has no native ``<select>``: a Select is a ``combobox`` button that
shows the chosen option's label and opens a listbox of ``option`` rows.
"""
from __future__ import annotations


def choose(trigger, label) -> None:
    """Open the Select behind ``trigger`` and pick the option named ``label``.

    ``label`` is the option's exact text or a compiled pattern.
    """
    trigger.click()
    exact = {"exact": True} if isinstance(label, str) else {}
    trigger.page.get_by_role("option", name=label, **exact).click()

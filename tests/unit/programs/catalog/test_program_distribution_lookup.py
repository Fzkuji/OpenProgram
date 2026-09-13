from __future__ import annotations

from importlib import metadata

from openprogram.programs import _programs


def test_known_program_uses_direct_distribution_lookup(monkeypatch) -> None:
    seen: list[str] = []

    def lookup(name: str):
        seen.append(name)
        return object()

    monkeypatch.setattr(metadata, "distribution", lookup)
    monkeypatch.setattr(
        metadata,
        "packages_distributions",
        lambda: (_ for _ in ()).throw(AssertionError("full scan is too slow")),
    )

    assert _programs._has_installed_distribution("gui_harness") is True
    assert seen == ["gui-agent-harness"]


def test_missing_program_distribution_is_not_installed(monkeypatch) -> None:
    def missing(name: str):
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(metadata, "distribution", missing)

    assert _programs._has_installed_distribution("research_harness") is False

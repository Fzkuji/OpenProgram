"""The system prompt tells a tool-using agent how to write a Program."""
from types import SimpleNamespace

import pytest

from openprogram.runtime import interface_link
from openprogram.runtime.context import components


@pytest.fixture(autouse=True)
def _documentation_link(monkeypatch):
    """The Server's link resolves documentation pages to files; the block
    names the authoring guide only through that port. The link that was
    registered before (the Server's, once it is loaded) is back afterwards."""
    link = SimpleNamespace(document_file=lambda page: f"/installed/docs/{page}.html.md")
    monkeypatch.setattr(interface_link, "_link", link)


def _with_tools(names):
    token = components._tools_var.set([SimpleNamespace(name=name) for name in names])
    try:
        return components._build_programs(None)
    finally:
        components._tools_var.reset(token)


def test_absent_without_tools():
    assert components._build_programs(None) == ""
    assert _with_tools([]) == ""


def test_names_the_package_root_the_guide_and_the_order_of_work():
    block = _with_tools(["read", "write", "bash", "list_agents", "read_conversation"])
    assert block.startswith("<programs>") and block.endswith("</programs>")
    assert "programs/<name>/" in block
    assert "Read the authoring guide at /installed/docs/capabilities/workflows/authoring.html.md" in block
    assert "`list_agents`, `read_conversation`" in block
    assert "recorded demonstration" in block
    assert "openprogram programs check" in block
    assert "`list_agents`" not in _with_tools(["read", "write"])

"""Focused regression tests for message block serialization."""
from openprogram.webui.messages import Block


def test_citation_block_to_dict_trims_unrelated_fields():
    block = Block(type="citation", citation_source="docs", citation_title="API")

    data = block.to_dict()

    assert data["citation_source"] == "docs"
    assert data["citation_title"] == "API"
    assert "text" not in data
    assert "image_uri" not in data
    assert "tool_call_id" not in data



def test_error_block_to_dict_keeps_text_only():
    block = Block(type="error", text="boom")

    data = block.to_dict()

    assert data["text"] == "boom"
    assert "tool_call_id" not in data
    assert "image_uri" not in data
    assert "citation_source" not in data

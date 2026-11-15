"""Strict nullable pagination reaches the real read tool through Runtime adaptation."""

import asyncio

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from openprogram.runtime.agent.runtime import _adapt_tools
from openprogram.programs.tools.files.read import read
from openprogram.models.providers.schema.strict import fixup_for_strict


def _fixture(tmp_path, kind):
    path = tmp_path / ("read." + kind)
    if kind == "txt":
        # Short lines, so the default 2,000 fit in one read's page.
        path.write_text("\n".join(f"L{i}" for i in range(1, 2003)))
    else:
        writer = PdfWriter()
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                 NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        for index in range(25):
            page = writer.add_blank_page(width=300, height=300)
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
            })
            stream = DecodedStreamObject()
            stream.set_data(f"BT /F1 12 Tf 10 200 Td (page {index + 1}) Tj ET".encode())
            page[NameObject("/Contents")] = stream
        writer.write(path)
    return path


def _run(path, arguments):
    tool = _adapt_tools([read])[0]
    result = asyncio.run(tool.execute("read-null", {"file_path": str(path), **arguments}, None, None))
    assert not result.is_error
    return "\n".join(block.text for block in result.content if hasattr(block, "text"))


@pytest.mark.parametrize("kind", ["txt", "pdf"])
@pytest.mark.parametrize("arguments", [{"offset": None}, {"limit": None}, {"offset": None, "limit": None}])
def test_strict_nullable_read_pagination_matches_omitted_defaults(tmp_path, kind, arguments):
    schema = fixup_for_strict(_adapt_tools([read])[0].parameters)
    for field in arguments:
        assert field in schema["required"]
        assert "null" in schema["properties"][field]["type"]
    path = _fixture(tmp_path, kind)
    default = _run(path, {})
    assert ("lines 1-2000 of 2002" if kind == "txt" else "pages 1-20 of 25") in default
    assert _run(path, arguments) == default


@pytest.mark.parametrize("kind", ["txt", "pdf"])
def test_read_explicit_pagination_is_preserved(tmp_path, kind):
    output = _run(_fixture(tmp_path, kind), {"offset": 2, "limit": 2})
    assert ("lines 2-3 of 2002" if kind == "txt" else "pages 2-3 of 25") in output

"""Every way a person's picture enters a conversation keeps it for later
requests: a message of pictures alone, a retry, an edit and a steer
(``store.session.media``, ``docs/reference/design/context/overview.html, "Pictures and long messages"``)."""
from __future__ import annotations

import base64

from openprogram.models.providers.types import ImageContent, TextContent
from openprogram.runtime.context.render import render_dag_messages
from openprogram.runtime.conversation.branch_fork import plan_fork
from openprogram.state.store.session import media
from openprogram.state.store.session.nodes import Call, Graph, ROLE_USER

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * 32).decode()
JPEG = base64.b64encode(b"\xff\xd8\xff" + b"y" * 32).decode()


def test_a_message_of_pictures_alone_renders_them_with_no_text_block(tmp_path):
    records = media.records_for_attachments(tmp_path, [{"type": "image", "data": PNG,
                                                        "media_type": "image/png"}], 0)
    graph = Graph()
    node = graph.add(Call(role=ROLE_USER, output="", metadata={"media": records}))
    [message] = render_dag_messages(graph, [node.id], str(tmp_path))
    assert [type(block) for block in message.content] == [ImageContent]
    assert message.content[0].data == PNG


def test_a_message_with_text_puts_its_pictures_after_the_text(tmp_path):
    records = media.records_for_attachments(tmp_path, [{"type": "image", "data": PNG,
                                                        "media_type": "image/png"}], 1)
    graph = Graph()
    node = graph.add(Call(role=ROLE_USER, output="what is this", metadata={"media": records}))
    [message] = render_dag_messages(graph, [node.id], str(tmp_path))
    assert [type(block) for block in message.content] == [TextContent, ImageContent]


def test_an_empty_message_whose_picture_is_gone_is_not_sent(tmp_path):
    records = media.records_for_attachments(tmp_path, [{"type": "image", "data": PNG,
                                                        "media_type": "image/png"}], 0)
    for path in (tmp_path / media.DIRNAME).iterdir():
        path.unlink()
    graph = Graph()
    node = graph.add(Call(role=ROLE_USER, output="", metadata={"media": records}))
    assert render_dag_messages(graph, [node.id], str(tmp_path)) == []


def test_a_retry_carries_the_pictures_and_an_edit_does_not():
    kept = [{"sha": "a" * 64, "mime": "image/png", "index": 1}]
    messages = [{"id": "u1", "role": "user", "content": "look", "predecessor": "ROOT", "media": kept},
                {"id": "a1", "role": "assistant", "content": "a dot", "predecessor": "u1"}]
    assert plan_fork(messages, "a1", None).message["media"] == kept
    assert "media" not in plan_fork(messages, "a1", "look again").message


def test_a_retry_sends_the_kept_pictures_in_their_order(tmp_path):
    records = media.records_for_attachments(tmp_path, [
        {"type": "image", "data": PNG, "media_type": "image/png"},
        {"type": "image", "data": JPEG, "media_type": "image/jpeg"}], 1)
    attachments = media.attachments_from_records(list(reversed(records)), tmp_path)
    assert [(a["data"], a["media_type"]) for a in attachments] == [(PNG, "image/png"), (JPEG, "image/jpeg")]
    (tmp_path / media.DIRNAME / f"{records[0]['sha']}.png").unlink()
    records[0]["path"] = str(tmp_path / "gone.png")
    assert [a["data"] for a in media.attachments_from_records(records, tmp_path)] == [JPEG]


def test_a_steer_keeps_its_pictures_after_its_text(tmp_path):
    """A steer is delivered as text, then its pictures; the node records
    them at those indexes so the next request renders the same message."""
    blocks = [TextContent(text="and this one"), ImageContent(data=PNG, mime_type="image/png")]
    records = media.records_for_blocks(tmp_path, blocks)
    graph = Graph()
    node = graph.add(Call(role=ROLE_USER, output="and this one", metadata={"media": records}))
    [message] = render_dag_messages(graph, [node.id], str(tmp_path))
    assert [(type(b), getattr(b, "data", None)) for b in message.content] == [
        (TextContent, None), (ImageContent, PNG)]

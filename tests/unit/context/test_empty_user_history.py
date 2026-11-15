from openprogram.state.store.session.nodes import Graph, Call
from openprogram.runtime.context.render import render_dag_messages
from openprogram.models.providers.types import Context, Model, UserMessage, TextContent, ImageContent
from openprogram.models.providers.adapters.openai_completions.openai_completions import _build_messages


def test_root_and_empty_history_do_not_become_user_turns():
    graph = Graph()
    graph.add(Call(id="ROOT", role="user", output=""))
    graph.add(Call(id="blank", role="user", output="  "))
    graph.add(Call(id="question", role="user", output="Continue the task"))
    messages = render_dag_messages(graph, ["ROOT", "blank", "question"])
    assert len(messages) == 1
    assert "Continue the task" in messages[0].content[0].text


def test_completions_omits_empty_users_but_keeps_image_only_turns():
    context = Context(messages=[UserMessage(content="", timestamp=0),
        UserMessage(content=[TextContent(text="  ")], timestamp=0),
        UserMessage(content=[], timestamp=0),
        UserMessage(content=[ImageContent(data="YWJj", mime_type="image/png")], timestamp=0),
        UserMessage(content="Continue", timestamp=0)])
    model = Model(id="test", name="test", provider="test", api="openai-completions", base_url="https://example.com")
    messages = _build_messages(context, model)
    assert len(messages) == 2
    assert messages[0]["content"][0]["type"] == "image_url"
    assert messages[1]["content"] == "Continue"

"""Outbound formatting single point (_format.py).

Agent output is markdown; each platform gets its own wire format:
telegram HTML (always-valid, everything escaped), slack mrkdwn,
discord passthrough, wechat plain text.
"""
from __future__ import annotations

import pytest

from openprogram.interfaces.channels import _format


# ---------------------------------------------------------------------------
# Telegram — HTML
# ---------------------------------------------------------------------------

def test_telegram_bold_italic_code_link() -> None:
    out = _format.render(
        "telegram",
        "**bold** and *ital* plus `x < 1` see [docs](https://ex.com/a)",
    )
    assert "<b>bold</b>" in out
    assert "<i>ital</i>" in out
    assert "<code>x &lt; 1</code>" in out
    assert '<a href="https://ex.com/a">docs</a>' in out


def test_telegram_escapes_html_specials() -> None:
    out = _format.render("telegram", "a < b & c > d")
    assert out == "a &lt; b &amp; c &gt; d"


def test_telegram_fence_becomes_pre_and_is_protected() -> None:
    out = _format.render("telegram", "```py\nx = '**not bold**' < 2\n```")
    assert out.startswith("<pre>")
    assert "**not bold**" in out          # markdown untouched inside code
    assert "&lt; 2" in out                # but HTML-escaped
    assert "<b>" not in out


def test_telegram_heading_to_bold() -> None:
    assert _format.render("telegram", "## Results") == "<b>Results</b>"


# ---------------------------------------------------------------------------
# Slack — mrkdwn
# ---------------------------------------------------------------------------

def test_slack_bold_and_link_and_escape() -> None:
    out = _format.render("slack", "**bold** [site](https://ex.com) a<b&c")
    assert "*bold*" in out
    assert "<https://ex.com|site>" in out
    assert "a&lt;b&amp;c" in out


def test_slack_italic_star_to_underscore() -> None:
    assert _format.render("slack", "an *emphasis* here") == "an _emphasis_ here"


def test_slack_code_kept_and_escaped() -> None:
    out = _format.render("slack", "run `a && b`")
    assert out == "run `a &amp;&amp; b`"


# ---------------------------------------------------------------------------
# Discord — native markdown passthrough
# ---------------------------------------------------------------------------

def test_discord_passthrough() -> None:
    text = "**bold** `code` [x](https://e.co) <#123>"
    assert _format.render("discord", text) == text


# ---------------------------------------------------------------------------
# WeChat — plain text
# ---------------------------------------------------------------------------

def test_wechat_strips_markdown() -> None:
    out = _format.render(
        "wechat",
        "# Title\n**bold** *ital* `code` [site](https://ex.com) ~~gone~~",
    )
    assert out == "Title\nbold ital code site (https://ex.com) gone"


def test_wechat_fence_keeps_content_drops_backticks() -> None:
    out = _format.render("wechat", "```\nls -la\n```")
    assert "```" not in out
    assert "ls -la" in out


def test_unknown_platform_passthrough() -> None:
    assert _format.render("matrix", "**x**") == "**x**"


def test_strip_markdown_helper() -> None:
    assert _format.strip_markdown("**a** `b`") == "a b"


def test_visual_blocks_stay_in_the_app():
    from openprogram.interfaces.channels._format import VISUAL_ONLY_NOTE, drop_visuals, render, strip_markdown
    reply = "Here is the chart.\n\n```visual\n<div class=\"card\">x</div>\n<script>draw()</script>\n```\n\nDone."
    for platform in ("wechat", "telegram", "slack", "discord"):
        out = render(platform, reply)
        assert "<script>" not in out and "card" not in out and "Done." in out, platform
    assert strip_markdown(reply) == "Here is the chart.\n\nDone."
    assert drop_visuals("```visual\n<svg></svg>\n```") == VISUAL_ONLY_NOTE
    assert drop_visuals("```python\nvisual = 1\n```") == "```python\nvisual = 1\n```"


# ---------------------------------------------------------------------------
# Chunks of a split code block render as code
# ---------------------------------------------------------------------------

def test_split_code_block_renders_as_code_in_every_chunk() -> None:
    from openprogram.interfaces.channels._transport import _chunk

    body = "\n".join(f"**{i}** < x" for i in range(800))
    chunks = _chunk(f"```py\n{body}\n```", 4000)
    assert len(chunks) > 1
    for chunk in chunks:
        out = _format.render("telegram", chunk)
        assert out.startswith("<pre>") and out.endswith("</pre>")
        assert "<b>" not in out and "```" not in out


# ---------------------------------------------------------------------------
# flatten_tables
# ---------------------------------------------------------------------------

_TABLE = "Costs:\n\n| Item | Cost |\n|:---|---:|\n| **Tea** | 3 |\n| Milk | |\n\nDone."


def test_a_table_becomes_one_line_per_row():
    assert _format.flatten_tables(_TABLE) == "Costs:\n\nItem: **Tea** · Cost: 3\nItem: Milk\n\nDone."


@pytest.mark.parametrize("platform, row", [
    ("telegram", "Item: <b>Tea</b> · Cost: 3"),
    ("slack", "Item: *Tea* · Cost: 3"),
    ("discord", "Item: **Tea** · Cost: 3"),
    ("wechat", "Item: Tea · Cost: 3"),
])
def test_a_flattened_table_renders_on_every_platform(platform, row):
    rendered = _format.render(platform, _format.flatten_tables(_TABLE))
    assert row in rendered and "|" not in rendered


@pytest.mark.parametrize("text", [
    "```\n| a | b |\n|---|---|\n| 1 | 2 |\n```",
    "~~~md\n| a | b |\n|---|---|\n~~~",
    "a | b and a pipe, but no delimiter row\n\nnext",
    "| a | b |\n|---|\n| 1 | 2 |",
    "```\nnever closed\n| a | b |\n|---|---|\n",
    "Title | here\n---",
])
def test_what_is_not_a_table_is_left_alone(text):
    assert _format.flatten_tables(text) == text


def test_flatten_handles_edge_rows():
    assert _format.flatten_tables("a | b\n--- | ---\n1 | 2") == "a: 1 · b: 2"
    assert _format.flatten_tables("| a |\n|---|\n| x \\| y |\n") == "a: x | y\n"
    assert _format.flatten_tables("| a | b |\n|---|---|\n") == "a · b\n"
    assert _format.flatten_tables("| a |\n|---|\n| 1 | extra |") == "a: 1 · extra"
    once = _format.flatten_tables(_TABLE)
    assert _format.flatten_tables(once) == once


def test_a_table_with_no_text_is_not_turned_into_nothing():
    blank = "| |\n|---|\n"
    assert _format.flatten_tables(blank) == blank


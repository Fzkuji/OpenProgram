"""Outbound formatting single point (_format.py).

Agent output is markdown; each platform gets its own wire format:
telegram HTML (always-valid, everything escaped), slack mrkdwn,
discord passthrough, wechat plain text.
"""
from __future__ import annotations

from openprogram.channels import _format


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

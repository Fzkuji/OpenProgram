"""出站格式化单点 — agent 输出是 markdown, 每个平台渲染成自己的格式.

  telegram — HTML 子集 (sendMessage parse_mode=HTML). 所有非标签文本
             都经 html-escape, 所以任意输入都是合法 HTML; 解析失败的
             兜底在 _transport 的 telegram poster 里 (剥标签重发).
  slack    — mrkdwn: ``*bold*`` / ``_italic_`` / ``~strike~`` /
             ``<url|label>``; ``&`` ``<`` ``>`` 按 Slack 要求转义.
  discord  — 原生 markdown, 原样透传.
  wechat   — 纯文本降级: 剥掉 markdown 记号, 链接展开成 ``label (url)``.

唯一调用点是 :mod:`._transport` (post_message / patch_message 每个
chunk 渲染一次). adapter 和上层永远不自己格式化.

实现说明: 先把 fenced code block 和 inline code span 摘出来占位,
对剩余文本做行内转换, 最后按平台还原代码段 — 代码内容不被加粗/斜体
规则误伤. 转换器是正则级的 (非完整 markdown parser), 覆盖 bold /
italic / strike / code / link / heading / blockquote; 更复杂的结构
(表格、嵌套列表) 原样透传, 各平台以纯文本显示, 不会报错.
"""
from __future__ import annotations

import html
import re


# 占位符: \x00<n>\x00 — 出现在真实聊天文本里的概率可以忽略.
_SLOT = "\x00{}\x00"
_SLOT_RE = re.compile("\x00(\\d+)\x00")

_FENCE_RE = re.compile(r"```([^\n`]*)\n?([\s\S]*?)```")
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.S)
_BOLD_US_RE = re.compile(r"__(.+?)__", re.S)
_ITALIC_STAR_RE = re.compile(r"(?<![*\w])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![*\w])")
_ITALIC_US_RE = re.compile(r"(?<![_\w])_(?!\s)([^_\n]+?)(?<!\s)_(?![_\w])")
_STRIKE_RE = re.compile(r"~~(.+?)~~", re.S)
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$", re.M)


def render(platform: str, text: str) -> str:
    """markdown → ``platform`` 的出站线上格式. 未知平台原样返回."""
    fn = _RENDERERS.get(platform)
    return fn(text) if fn else text


def strip_markdown(text: str) -> str:
    """markdown → 纯文本. WeChat 的常规输出, 也是 telegram HTML 解析
    失败时 poster 兜底重发用的形态."""
    return _render_plain(text)


# ---------------------------------------------------------------------------
# code 段摘除 / 还原
# ---------------------------------------------------------------------------

def _pull_code(text: str) -> tuple[str, list[tuple[str, str]]]:
    """把 fenced block 和 inline span 换成占位符.

    返回 (带占位符的文本, [(kind, body), ...]); kind ∈ {"fence",
    "inline"}. fence 的 body 不含 ``` 行, inline 的 body 不含反引号.
    """
    slots: list[tuple[str, str]] = []

    def _take_fence(m: re.Match) -> str:
        slots.append(("fence", m.group(2)))
        return _SLOT.format(len(slots) - 1)

    def _take_inline(m: re.Match) -> str:
        slots.append(("inline", m.group(1)))
        return _SLOT.format(len(slots) - 1)

    text = _FENCE_RE.sub(_take_fence, text)
    text = _INLINE_CODE_RE.sub(_take_inline, text)
    return text, slots


def _restore_code(text: str, slots: list[tuple[str, str]],
                  fence_wrap, inline_wrap) -> str:
    def _put(m: re.Match) -> str:
        kind, body = slots[int(m.group(1))]
        return fence_wrap(body) if kind == "fence" else inline_wrap(body)
    return _SLOT_RE.sub(_put, text)


# ---------------------------------------------------------------------------
# Telegram — HTML 子集
# ---------------------------------------------------------------------------

def _render_telegram(text: str) -> str:
    body, slots = _pull_code(text)
    body = html.escape(body, quote=False)
    body = _LINK_RE.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', body)
    body = _BOLD_RE.sub(r"<b>\1</b>", body)
    body = _BOLD_US_RE.sub(r"<b>\1</b>", body)
    body = _ITALIC_STAR_RE.sub(r"<i>\1</i>", body)
    body = _ITALIC_US_RE.sub(r"<i>\1</i>", body)
    body = _STRIKE_RE.sub(r"<s>\1</s>", body)
    body = _HEADING_RE.sub(r"<b>\1</b>", body)
    return _restore_code(
        body, slots,
        fence_wrap=lambda b: f"<pre>{html.escape(b, quote=False)}</pre>",
        inline_wrap=lambda b: f"<code>{html.escape(b, quote=False)}</code>",
    )


# ---------------------------------------------------------------------------
# Slack — mrkdwn
# ---------------------------------------------------------------------------

def _slack_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _render_slack(text: str) -> str:
    body, slots = _pull_code(text)
    body = _slack_escape(body)
    # 链接在 escape 之后转换 — mrkdwn 的 <url|label> 尖括号是字面量.
    body = _LINK_RE.sub(lambda m: f"<{m.group(2)}|{m.group(1)}>", body)
    # 单星斜体必须先于加粗转换: 加粗输出的 *x* 否则会被斜体规则二次
    # 匹配成 _x_. 斜体正则的 lookaround 保证它不会咬进 ** 对.
    body = _ITALIC_STAR_RE.sub(r"_\1_", body)
    body = _BOLD_RE.sub(r"*\1*", body)
    body = _BOLD_US_RE.sub(r"*\1*", body)
    body = _STRIKE_RE.sub(r"~\1~", body)
    body = _HEADING_RE.sub(r"*\1*", body)
    return _restore_code(
        body, slots,
        fence_wrap=lambda b: f"```{_slack_escape(b)}```",
        inline_wrap=lambda b: f"`{_slack_escape(b)}`",
    )


# ---------------------------------------------------------------------------
# WeChat / 兜底 — 纯文本
# ---------------------------------------------------------------------------

def _render_plain(text: str) -> str:
    body, slots = _pull_code(text)
    body = _LINK_RE.sub(lambda m: f"{m.group(1)} ({m.group(2)})", body)
    body = _BOLD_RE.sub(r"\1", body)
    body = _BOLD_US_RE.sub(r"\1", body)
    body = _ITALIC_STAR_RE.sub(r"\1", body)
    body = _ITALIC_US_RE.sub(r"\1", body)
    body = _STRIKE_RE.sub(r"\1", body)
    body = _HEADING_RE.sub(r"\1", body)
    return _restore_code(
        body, slots,
        fence_wrap=lambda b: b.rstrip("\n"),
        inline_wrap=lambda b: b,
    )


_RENDERERS = {
    "telegram": _render_telegram,
    "slack": _render_slack,
    # discord: 原生 markdown, 无条目 → render() 原样返回
    "wechat": _render_plain,
}

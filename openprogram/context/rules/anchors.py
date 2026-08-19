"""锚点选择 — summary 不光生成文字, 还挑关键 item 保留原文.

为啥这是我们独有的: Claude Code / OpenCode / Hermes 都是线性 history,
压缩时只能整段砍, 没法 "挑节点保留". 我们的 DAG + ContextCommit 结构允许
按引用 / 错误 / 长度等信号给每个 item 打分, 挑出 3-5 个高价值节点
保留原文, 让 LLM 既看到 summary 又看到几条关键原文 — 等于给 summary
配上"证据 footnote", 降低摘要丢信息的风险。

打分纯启发式 — 不调 LLM, 不查外部状态, 只看 item 本身的属性. 想加
信号往 _score_item 里塞 if 即可.
"""
from __future__ import annotations

from openprogram.context.commit.types import ContextItem


# 默认每次 summary 最多挑 5 个锚点 — 多了等于没压缩, 少了起不到
# "证据 footnote" 的作用. 5 是经验值, 不是定理.
ANCHOR_BUDGET = 5

# 总分低于这个的不挑 — 纯水的 item (短 grep / 空 list) 没必要锚。
MIN_SCORE = 1.0


# 哪些 tool 算"读外部信息且难重现" — 这类原文丢了恢复成本最高,
# 因为外部世界可能变了 (web_search 结果会刷新, 文件会改). 给高权重.
_EXTERNAL_INFO_TOOLS = {
    "read", "read_file", "view", "open",
    "web_search", "web_fetch", "fetch", "curl",
    "bash",  # bash 输出可能是一次性的 (ps, date, log tail), 也算难重现
}

# 哪些 tool 算"轻量可重跑" — grep / list 这种, 原文丢了重跑一次就回来.
# 给低权重就好。
_LIGHTWEIGHT_TOOLS = {
    "grep", "rg", "list", "ls", "glob", "find",
}


def _score_item(item: ContextItem) -> float:
    """启发式打分: 不同信号加权求和, 越高越值得当锚点.

    各权重的设计理由都写在 inline 注释里; 改了请同步注释。
    """
    score = 0.0

    # 把 source_node_id 当 tool name 用做粗判断 — 实际项目里 ContextItem
    # 没直接存 tool name, role='tool' 时大概率能从 node_id 前缀推出来.
    # 这里走保守路线: 看 role + rendered 内容找信号.
    node_id_lower = item.source_node_id.lower()
    rendered = item.rendered or ""
    rendered_lower = rendered.lower()

    # 信号 1: 外部信息读取 (read/web_*/bash) → +2. 这类原文难重现.
    if any(tok in node_id_lower for tok in _EXTERNAL_INFO_TOOLS):
        score += 2.0
    elif item.role == "tool" and (
        "http" in rendered_lower[:200] or rendered.startswith("---")
    ):
        # tool output 前 200 字符里有 url / file diff 头, 多半是外部信息.
        score += 2.0

    # 信号 2: 轻量可重跑工具 → +0.5. 给点分但很低 (代表"有点用但丢了
    # 不心疼").
    if any(tok in node_id_lower for tok in _LIGHTWEIGHT_TOOLS):
        score += 0.5

    # 信号 3: rendered 很长 → +1. 信息密度高的 item 丢了损失更大.
    # 阈值 1000 char ≈ 250 tokens, 经验上短于这个的 tool output 多半
    # 是 "Done"/"OK" 之类的水内容。
    if len(rendered) > 1000:
        score += 1.0

    # 信号 4: 错误标记 → +1.5. error 信息珍贵 — 它记录了"什么不能这样
    # 做", 摘要里很难复现细节 (stack trace, 具体报错), 必须保原文.
    # 因为 ContextItem 当前没专门的 is_error 字段, 走启发式: reason
    # 含 'error' 或 rendered 前 100 字符含 'Error'/'Traceback'.
    if "error" in (item.reason or "").lower():
        score += 1.5
    elif "Error" in rendered[:200] or "Traceback" in rendered[:200]:
        score += 1.5

    # 信号 5: assistant 消息 → +1. LLM 的关键决策 (我准备 X、我看到 Y、
    # 我的计划是 Z), 这些 framing 在 summary 里会被压平。
    if item.role == "assistant":
        score += 1.0

    return score


def select_anchors(
    items: list[ContextItem],
    budget: int = ANCHOR_BUDGET,
) -> list[int]:
    """从 items 里挑出锚点 indices, 按打分降序取前 budget 个.

    只考虑 unlocked 且 state ∈ {full, aged, cleared} 的 item; 已经被
    pin / summarize / summarized 锁住的不参与 (要么必保留要么已合并,
    锚不锚没意义).

    返回 indices 列表 (升序, 方便调用方按位置迭代). 长度 ≤ budget.
    分数低于 MIN_SCORE 的丢掉。
    """
    candidates: list[tuple[float, int]] = []
    for idx, item in enumerate(items):
        if item.locked:
            continue
        if item.state not in ("full", "aged", "cleared"):
            continue
        s = _score_item(item)
        if s >= MIN_SCORE:
            candidates.append((s, idx))

    # 按分数降序, 同分按位置升序 (老的优先 — 老的更可能在 summary
    # 范围内, 更需要"证据"补)
    candidates.sort(key=lambda t: (-t[0], t[1]))
    picked = [idx for _, idx in candidates[:budget]]
    picked.sort()
    return picked

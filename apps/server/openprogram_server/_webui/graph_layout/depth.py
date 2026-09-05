"""Depth (row index) per node = 它在图里占的那一行。

行号语义是**每个节点独占一行、按发生顺序自上而下铺开**，而不是"到根的
跳数"。跳数版本会让同一个父的多个孩子叠在同一行——spec.html 场景 5
（gui_agent 的两个子调用在行 4 和行 6）、场景 11（一轮里三个工具在行
2/3/4，"同列、逐行排"）、场景 6（工具占了行 3，把「追问」挤到行 4）
画的都不是那样。

算法 = 按结构父串成的树做前序遍历，每访问一个节点发一个新行号：

  * 根 depth 0；
  * 一个节点的孩子按 seq 排序，逐个往下发行号；
  * 一棵子树占满自己的行之后，下一个兄弟才接着往下——所以「上面的兄弟
    展开了多少行」会如实把下面的兄弟推下去（场景 6 ↔ 场景 7 的纵向
    紧凑就是这条规则的直接结果：工具收起 → 它那一行没人占 → 下面整体
    上移）。

两个"不占新行"的例外，都是横向长出去的分支，边是水平的：

  * **fork 兄弟**（retry/改写）与它分叉出来的那个位置**同一行**
    （场景 3：`你好` 在行 1，改写出来的 fork user 也在行 1；场景 6/7
    同理）。第一个兄弟继续本行往下，后面的兄弟各自回到分叉行起步。
  * **spawn 分支根**与发起 spawn 的那轮**同一行**（场景 10：spawn 在
    行 3 → 分支根行 3 → B 干完行 4）。

两者的子树都从各自的起始行继续往下长。
"""
from __future__ import annotations

from ._common import (
    predecessor_of, caller_of, is_root, is_top_program_run, retry_source, ts,
)


def _is_spawn_root(m: dict) -> bool:
    return bool(m.get("source") == "agent_spawn" and not m.get("predecessor"))


def compute_depth(
    by_id: dict[str, dict],
    call_children: dict[str, list[str]],
    fork_siblings: dict[str, list[str]] | None = None,
) -> dict[str, float]:
    """每个节点的 depth = 前序遍历里分配给它的行号。

    call_children / fork_siblings 保留在签名里兼容调用方；本算法自己按
    by_id 上的 predecessor/caller 重建父子关系，避免依赖调用方是否把两
    种边合并过。
    """

    def _parent(nid: str) -> str | None:
        m = by_id.get(nid)
        if m is None:
            return None
        # 对话前驱优先，没有则 caller（分支首节点靠 caller=ROOT 挂根，
        # turn 内 sub-call 靠 caller 指向其 llm）。只有指向图内节点才算父。
        p = predecessor_of(by_id, m) or caller_of(by_id, m)
        return p if (p and p in by_id) else None

    # 防御性防环：父指针成环时（正常 DAG 不会）把环上的节点当孤儿根，
    # 否则下面的遍历会无限递归。
    parent: dict[str, str | None] = {}
    for nid in by_id:
        p, seen = _parent(nid), {nid}
        while p is not None and p not in seen:
            seen.add(p)
            p = _parent(p)
        parent[nid] = None if p is not None else _parent(nid)

    children: dict[str, list[str]] = {}
    for nid, p in parent.items():
        if p is not None:
            children.setdefault(p, []).append(nid)
    for kids in children.values():
        kids.sort(key=lambda x: ts(by_id, x))

    depth: dict[str, float] = {}
    roots = sorted((n for n, p in parent.items() if p is None),
                   key=lambda x: ts(by_id, x))

    def _walk(nid: str, row: float) -> float:
        """给 nid 发行号 row，返回这棵子树用掉的最后一行 +1（下一空行）。"""
        depth[nid] = row
        nxt = row + 1.0
        kids = children.get(nid, [])
        # 同一个 predecessor 下的第 2 个及以后的对话层兄弟 = fork（retry/
        # 改写）。它们不排在前一个兄弟的子树下面，而是回到分叉行横着长
        # 出去（场景 3/6/7）。第一个兄弟正常继续本行往下。
        # fork 的"分叉行" = 被改写的那个兄弟（第一个对话层孩子）所在的
        # 行，不是共同 predecessor 的行：场景 3 里 ROOT 在行 0、`你好`
        # 在行 1，改写出来的 fork user 和 `你好` 平齐，也在行 1。
        fork_row: float | None = None
        for kid in kids:
            km = by_id[kid]
            is_conv_kid = predecessor_of(by_id, km) == nid
            source = retry_source(km)
            root_program = is_root(by_id[nid]) and is_top_program_run(km)
            # spawn 根与发起它的那轮同一行；但挂在 ROOT 下的（跨会话
            # spawn 落到目标会话）是本会话的头一条对话，照常往下一行走。
            if root_program:
                start = depth[source] if source and source in depth else nxt
            elif _is_spawn_root(km) and not is_root(by_id[nid]):
                start = row              # 与发起 spawn 的那轮同一行
            elif is_conv_kid and fork_row is not None:
                start = fork_row         # 与被改写的兄弟同一行
            else:
                start = nxt
            if is_conv_kid and fork_row is None and not root_program:
                fork_row = start
            nxt = max(nxt, _walk(kid, start))
        return nxt

    row = 0.0
    for r in roots:
        row = _walk(r, row)

    return depth

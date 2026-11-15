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
    original_parent = {nid: _parent(nid) for nid in by_id}
    cyclic: dict[str, bool] = {}
    for nid in by_id:
        path: set[str] = set()
        cur = nid
        while cur is not None and cur not in cyclic and cur not in path:
            path.add(cur)
            cur = original_parent[cur]
        enters_cycle = cur is not None and (cur in path or cyclic[cur])
        for visited in path:
            cyclic[visited] = enters_cycle
    parent = {nid: None if cyclic[nid] else p
              for nid, p in original_parent.items()}

    children: dict[str, list[str]] = {}
    for nid, p in parent.items():
        if p is not None:
            children.setdefault(p, []).append(nid)
    for kids in children.values():
        kids.sort(key=lambda x: ts(by_id, x))

    depth: dict[str, float] = {}
    roots = sorted((n for n, p in parent.items() if p is None),
                   key=lambda x: ts(by_id, x))

    # Each frame stores a suspended preorder visit. Returning from a child
    # advances its parent's next free row, exactly as the recursive walk did.
    row = 0.0
    for root in roots:
        depth[root] = row
        stack = [[root, row, row + 1.0, None, iter(children.get(root, []))]]
        while stack:
            nid, current_row, nxt, fork_row, kids = stack[-1]
            kid = next(kids, None)
            if kid is None:
                stack.pop()
                if stack:
                    stack[-1][2] = max(stack[-1][2], nxt)
                else:
                    row = nxt
                continue
            km = by_id[kid]
            is_conv_kid = predecessor_of(by_id, km) == nid
            source = retry_source(km)
            root_program = is_root(by_id[nid]) and is_top_program_run(km)
            if root_program:
                start = depth[source] if source and source in depth else nxt
            elif _is_spawn_root(km) and not is_root(by_id[nid]):
                start = current_row
            elif is_conv_kid and fork_row is not None:
                start = fork_row
            else:
                start = nxt
            if is_conv_kid and fork_row is None and not root_program:
                stack[-1][3] = start
            depth[kid] = start
            stack.append([kid, start, start + 1.0, None,
                          iter(children.get(kid, []))])

    return depth

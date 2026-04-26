"""
Context 树的持久化 —— 序列化 / 反序列化 / 崩溃恢复。

不是范式原语，是工程性设施：长任务可能需要落盘、重启后恢复。

对外 API 保持不变：`ctx.save(path)` / `Context.from_jsonl(path)` 仍然是
Context 类的方法（在 context.py 里），但实现逻辑搬到这里。
"""

from __future__ import annotations

import json
import os
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from openprogram.agentic_programming.context import Context


# ---------------------------------------------------------------------------
# Serialize
# ---------------------------------------------------------------------------

def to_dict(ctx: "Context") -> dict:
    """把整棵 Context 树序列化成一个嵌套 dict（JSON 导出用）。"""
    return {
        "path": ctx.path,
        "name": ctx.name,
        "node_type": ctx.node_type,
        "prompt": ctx.prompt,
        "params": {k: (getattr(v, '__name__', None) or str(v)) if callable(v) else v
                   for k, v in (ctx.params or {}).items()
                   if k not in ("runtime", "callback")} if ctx.params else {},
        "output": ctx.output,
        "raw_reply": ctx.raw_reply,
        "attempts": ctx.attempts,
        "error": ctx.error,
        "status": ctx.status,
        "expose": ctx.expose,
        "source_file": ctx.source_file,
        "start_time": ctx.start_time,
        "end_time": ctx.end_time,
        "duration_ms": ctx.duration_ms,
        "children": [to_dict(c) for c in ctx.children],
    }


def from_dict(data: dict, parent: Optional["Context"] = None) -> "Context":
    """`to_dict` 的反函数 —— 从 dict 重建 Context 树。"""
    from openprogram.agentic_programming.context import Context
    ctx = Context(
        name=data.get("name", "unknown"),
        prompt=data.get("prompt", ""),
        params=data.get("params"),
        parent=parent,
        expose=data.get("expose", "io"),
        start_time=data.get("start_time"),
        node_type=data.get("node_type", "function"),
    )
    ctx.output = data.get("output")
    ctx.raw_reply = data.get("raw_reply")
    ctx.attempts = data.get("attempts", [])
    ctx.error = data.get("error")
    ctx.status = data.get("status", "running")
    ctx.source_file = data.get("source_file", "")
    ctx.end_time = data.get("end_time") or 0.0
    if not ctx.end_time and data.get("duration_ms") is not None and ctx.start_time:
        ctx.end_time = ctx.start_time + data["duration_ms"] / 1000.0
    for child_data in data.get("children", []):
        child = from_dict(child_data, parent=ctx)
        ctx.children.append(child)
    return ctx


def to_records(ctx: "Context", tree_depth: int = 0) -> list[dict]:
    """把树拍平成 list of dicts（JSONL 导出用，每个节点一行）。"""
    node = to_dict(ctx)
    node["depth"] = tree_depth
    records = [node]
    for c in ctx.children:
        records.extend(to_records(c, tree_depth + 1))
    return records


def to_event_records(ctx: "Context") -> list[dict]:
    """把树拍成 enter/exit 事件记录（用于崩溃恢复）。"""
    enter = {
        "event": "enter",
        "path": ctx.path,
        "name": ctx.name,
        "node_type": ctx.node_type,
        "prompt": ctx.prompt,
        "params": {k: (getattr(v, '__name__', None) or str(v)) if callable(v) else v
                   for k, v in (ctx.params or {}).items()
                   if k not in ("runtime", "callback")} if ctx.params else {},
        "expose": ctx.expose,
        "ts": ctx.start_time,
    }
    records = [enter]
    for child in ctx.children:
        records.extend(to_event_records(child))
    records.append({
        "event": "exit",
        "path": ctx.path,
        "status": ctx.status,
        "output": ctx.output,
        "raw_reply": ctx.raw_reply,
        "attempts": ctx.attempts,
        "error": ctx.error,
        "duration_ms": ctx.duration_ms,
        "ts": ctx.end_time,
    })
    return records


# ---------------------------------------------------------------------------
# Save / Load
# ---------------------------------------------------------------------------

def save(ctx: "Context", path: str | os.PathLike[str]) -> None:
    """
    把整棵树落盘。

    .md    → 人类可读的树状图（同 tree()）
    .json  → 嵌套 JSON（一个对象）
    .jsonl → 每个节点一行，机器可读
    """
    path_str = os.fspath(path)
    path_lower = path_str.lower()
    os.makedirs(os.path.dirname(os.path.abspath(path_str)), exist_ok=True)
    if path_lower.endswith(".md"):
        with open(path_str, "w", encoding="utf-8") as f:
            f.write(ctx.tree(color=False))
    elif path_lower.endswith(".json"):
        with open(path_str, "w", encoding="utf-8") as f:
            json.dump(to_dict(ctx), f, ensure_ascii=False, default=str, indent=2)
    elif path_lower.endswith(".jsonl"):
        with open(path_str, "w", encoding="utf-8") as f:
            for record in to_records(ctx):
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    else:
        raise ValueError(
            f"Unsupported file extension: {path_str}. Use .md, .json, or .jsonl."
        )


def from_jsonl(path: str | os.PathLike[str]) -> "Context":
    """从 JSONL 重建 Context 树。兼容事件流和 save() 的扁平导出。"""
    from openprogram.agentic_programming.context import Context
    path_str = os.fspath(path)
    with open(path_str, encoding="utf-8") as f:
        records = []
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not records:
        raise ValueError("No valid records found in JSONL file")

    if all("event" not in record for record in records):
        return _from_flat_jsonl_records(records)

    nodes: dict[str, Context] = {}
    root: Optional[Context] = None

    def _parent_path(node_path: str) -> Optional[str]:
        return node_path.rsplit("/", 1)[0] if "/" in node_path else None

    for record in records:
        node_path = record.get("path")
        if not node_path:
            continue

        if record.get("event") == "enter":
            parent = nodes.get(_parent_path(node_path))
            ctx = Context(
                name=record.get("name", node_path.rsplit("/", 1)[-1].split("_")[0]),
                prompt=record.get("prompt", ""),
                params=record.get("params") or {},
                parent=parent,
                expose=record.get("expose", "io"),
                start_time=record.get("ts", 0.0),
                node_type=record.get("node_type", "function"),
            )
            if parent is not None:
                parent.children.append(ctx)
            else:
                root = ctx
            nodes[node_path] = ctx
        elif record.get("event") == "exit":
            ctx = nodes.get(node_path)
            if ctx is None:
                continue
            ctx.status = record.get("status", ctx.status)
            ctx.output = record.get("output")
            ctx.raw_reply = record.get("raw_reply")
            ctx.attempts = record.get("attempts") or []
            ctx.error = record.get("error") or ""
            ctx.end_time = record.get("ts") or (
                (ctx.start_time + (record["duration_ms"] / 1000.0))
                if record.get("duration_ms") is not None and ctx.start_time
                else 0.0
            )

    if root is None:
        raise ValueError("No valid records found in JSONL file")

    return root


def _from_flat_jsonl_records(records: list[dict]) -> "Context":
    """Rebuild a tree from save(...jsonl) flat node records."""
    from openprogram.agentic_programming.context import Context

    nodes: dict[str, Context] = {}
    root: Optional[Context] = None

    for record in records:
        node_path = record.get("path")
        if not node_path:
            continue

        parent_path = node_path.rsplit("/", 1)[0] if "/" in node_path else None
        parent = nodes.get(parent_path)
        ctx = Context(
            name=record.get("name", node_path.rsplit("/", 1)[-1].split("_")[0]),
            prompt=record.get("prompt", ""),
            params=record.get("params") or {},
            parent=parent,
            expose=record.get("expose", "io"),
            start_time=record.get("start_time"),
            node_type=record.get("node_type", "function"),
        )
        ctx.output = record.get("output")
        ctx.raw_reply = record.get("raw_reply")
        ctx.attempts = record.get("attempts") or []
        ctx.error = record.get("error") or ""
        ctx.status = record.get("status", "running")
        ctx.source_file = record.get("source_file", "")
        ctx.end_time = record.get("end_time") or 0.0

        if not ctx.end_time and record.get("duration_ms") is not None and ctx.start_time:
            ctx.end_time = ctx.start_time + record["duration_ms"] / 1000.0

        if parent is not None:
            parent.children.append(ctx)
        else:
            root = ctx
        nodes[node_path] = ctx

    if root is None:
        raise ValueError("No valid records found in JSONL file")

    return root

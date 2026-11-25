"""Record what one workflow run did: the roles it was configured with, every stage's
context and output, and where the results went.

A Trail is plain code. It makes no model call and changes nothing the model sees; a
workflow records a stage after (or around) doing it, then saves the trail next to its
report as run.json and run.md. Role calls themselves are still made by the workflow
function that owns the docstring, so the framework reads the right instructions.
"""

import contextvars
import inspect
import json
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

MAX_CHARS = 200_000     # one context block or output; longer text is cut and marked
# Version of the run.json layout. Readers (the app) check it; raise it on any breaking change.
SCHEMA = 1

# Live listeners: an app hosting a run passes every stage event on to its user as it
# happens. Module-wide, not per context, because workflows run model calls in threads.
_listeners: list = []
_lock = threading.Lock()


@contextmanager
def listen(callback):
    """Call callback(event) for every stage any Trail records while the block runs.
    An event is {workflow, stage, kind, event: start|done, ...}; a failing callback
    never stops the workflow."""
    with _lock:
        _listeners.append(callback)
    try:
        yield
    finally:
        with _lock:
            _listeners.remove(callback)


def emit(event: dict) -> None:
    with _lock:
        callbacks = list(_listeners)
    for callback in callbacks:
        try:
            callback(event)
        except Exception:  # noqa: S110  a failing listener never stops the workflow
            pass


# Spend: the host meters every model call; a hook adds each one to the stage that made it.
_tally: contextvars.ContextVar = contextvars.ContextVar("auto_research_tally", default=None)
_hooked = False
SPEND = ("calls", "input_tokens", "output_tokens", "cache_read_tokens", "cost")


def blank() -> dict:
    return {k: 0 for k in SPEND}


def _count(event) -> None:
    tally = _tally.get()
    if tally is None:
        return
    with _lock:
        tally["calls"] += 1
        tally["input_tokens"] += int(getattr(event, "input_tokens", 0) or 0)
        tally["output_tokens"] += int(getattr(event, "output_tokens", 0) or 0)
        tally["cache_read_tokens"] += int(getattr(event, "cache_read_tokens", 0) or 0)
        tally["cost"] += float(getattr(event, "cost_total", 0) or 0)


def _hook() -> None:
    global _hooked
    if _hooked:
        return
    try:
        from openprogram.models.usage import register_usage_hook
    except ImportError:     # outside the host: nothing is metered
        return
    register_usage_hook(_count)
    _hooked = True


def add(total: dict, part: dict | None) -> dict:
    for k in SPEND:
        total[k] = total.get(k, 0) + (part or {}).get(k, 0)
    total["cost"] = round(total["cost"], 6)
    return total


def summary(facts: dict) -> dict:
    """Short facts for a live event: counts for lists, cut text for the rest."""
    out = {}
    for k, v in facts.items():
        if isinstance(v, (list, tuple, set, dict)):
            out[k] = len(v)
        elif isinstance(v, str):
            out[k] = cell(v, 160)
        else:
            out[k] = v
    return out


def describe_role(name: str, role) -> dict:
    """The configuration of one Agent role, as it stands when the workflow starts."""
    return {"name": name,
            "instructions": role.instructions,
            "effort": role.effort or "default",
            "model": role.model or "caller's model",
            "tools": [tool_name(t) for t in (role.tools or [])]}


def tool_name(tool) -> str:
    return getattr(tool, "__name__", None) or getattr(tool, "name", None) or repr(tool)


class Call:
    """What a model-call stage hands back: set output inside the with block."""
    output = None


class Trail:
    def __init__(self, workflow: str, args: dict, roles: dict):
        self.workflow = workflow
        self.args = jsonable(args)
        self.roles = {name: describe_role(name, role) for name, role in roles.items()}
        self._names = {id(role): name for name, role in roles.items()}
        self.started = now()
        self.stages: list[dict] = []
        self.outputs: dict = {}

    def step(self, stage: str, **facts) -> None:
        """A code stage: what it was given and what it produced, in a few facts."""
        self.stages.append({"stage": stage, "kind": "code", "at": now(), **jsonable(facts)})
        emit({"workflow": self.workflow, "stage": stage, "kind": "code", "event": "done", **summary(facts)})

    @contextmanager
    def call(self, stage: str, role, function, ctx, schema: str = ""):
        """A model-call stage. Wrap the role call that `function` makes with its Context:

            with trail.call("judge", judge, judge_refs, ctx, schema="reference_verdicts") as rec:
                rec.output = judge("...", context=ctx, response_format=...)
        """
        _hook()
        rec = Call()
        tally = blank()
        token = _tally.set(tally)
        entry = {"stage": stage, "kind": "model", "at": now(),
                 "role": self._names.get(id(role), getattr(role, "__name__", repr(role))),
                 "function": function.__name__, "instructions": inspect.getdoc(function) or "",
                 "context": blocks(ctx), "schema": schema or None}
        start = time.monotonic()
        emit({"workflow": self.workflow, "stage": stage, "kind": "model", "event": "start", "role": entry["role"]})
        try:
            yield rec
        except BaseException as e:
            entry["error"] = repr(e)
            raise
        finally:
            _tally.reset(token)
            entry["usage"] = add(blank(), tally)
            entry["seconds"] = round(time.monotonic() - start, 2)
            entry["output"] = jsonable(rec.output)
            self.stages.append(entry)
            emit({"workflow": self.workflow, "stage": stage, "kind": "model", "event": "done",
                  "role": entry["role"], "seconds": entry["seconds"], "error": cell(entry.get("error") or "", 160)})

    def usage(self) -> dict:
        """What the run's model calls cost, as the host metered them."""
        total = blank()
        for s in self.stages:
            add(total, s.get("usage"))
        return total

    def save(self, out: Path, **outputs) -> Path:
        """Write run.json and run.md into out; outputs name the files the run produced."""
        self.outputs = jsonable({k: str(v) for k, v in outputs.items()})
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        run = {"schema": SCHEMA, "workflow": self.workflow, "started": self.started, "finished": now(),
               "args": self.args,
               "roles": self.roles, "stages": self.stages, "outputs": self.outputs, "usage": self.usage()}
        (out / "run.json").write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "run.md").write_text(markdown(run), encoding="utf-8")
        return out / "run.json"


def blocks(ctx) -> dict:
    """The named content of a Context as the model receives it, providers included."""
    resolve = getattr(ctx, "resolve_blocks", None)
    try:
        content = resolve() if callable(resolve) else dict(ctx.items())
    except TypeError:
        content = dict(ctx.items())
    if not isinstance(content, dict):
        content = dict(ctx.items())
    return {str(k): jsonable(v) for k, v in content.items()}


def jsonable(value):
    if isinstance(value, str):
        return value if len(value) <= MAX_CHARS else value[:MAX_CHARS] + f"… [cut, {len(value)} chars]"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [jsonable(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return repr(value)


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def markdown(run: dict) -> str:
    lines = [f"# 运行记录：{run['workflow']}", "",
             f"开始 {run['started']}　结束 {run['finished']}", "",
             "## 参数", "", "```json", json.dumps(run["args"], ensure_ascii=False, indent=2), "```", "",
             "## 角色配置", ""]
    if run["roles"]:
        lines += ["| 角色 | 指令 | effort | 模型 | 工具 |", "|---|---|---|---|---|"]
        lines += [f"| {r['name']} | {cell(r['instructions'])} | {r['effort']} | {r['model']} | "
                  f"{', '.join(r['tools']) or '无'} |" for r in run["roles"].values()]
    else:
        lines.append("没有角色：这个 workflow 不调用模型。")
    lines += ["", "## 阶段", "", "| # | 阶段 | 类型 | 角色 / 内容 | 上下文 | 耗时 |", "|---|---|---|---|---|---|"]
    for i, s in enumerate(run["stages"], 1):
        if s["kind"] == "model":
            ctx = "、".join(f"{k}（{size(v)}）" for k, v in s["context"].items()) or "空"
            who = f"{s['role']} ← `{s['function']}`" + (f"，schema `{s['schema']}`" if s.get("schema") else "")
            err = f"　**出错** {cell(s['error'])}" if s.get("error") else ""
            lines.append(f"| {i} | {s['stage']} | 模型 | {who}{err} | {ctx} | {s['seconds']}s |")
        else:
            facts = "；".join(f"{k}={cell(json.dumps(v, ensure_ascii=False))}"
                              for k, v in s.items() if k not in ("stage", "kind", "at"))
            lines.append(f"| {i} | {s['stage']} | 代码 | {facts or '—'} | — | — |")
    spent = run.get("usage") or {}
    if spent.get("calls"):
        lines += ["", f"模型调用 {spent['calls']} 次，输入 {spent['input_tokens']} tokens，输出 {spent['output_tokens']} tokens，"
                      f"花费 ${spent['cost']:.4f}。"]
    lines += ["", "## 产出", ""]
    lines += [f"- {k}：`{v}`" for k, v in run["outputs"].items()] or ["无文件产出。"]
    lines += ["", "每个模型阶段的完整指令、上下文和输出在 run.json 的 `stages` 里。", ""]
    return "\n".join(lines)


def cell(text: str, limit: int = 120) -> str:
    text = " ".join(str(text).split()).replace("|", "\\|")
    return text if len(text) <= limit else text[:limit - 1] + "…"


def size(value) -> str:
    n = len(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))
    return f"{n} 字" if n < 1000 else f"{n / 1000:.1f}k 字"


__all__ = ["Call", "SPEND", "Trail", "add", "blank", "describe_role", "emit", "listen"]

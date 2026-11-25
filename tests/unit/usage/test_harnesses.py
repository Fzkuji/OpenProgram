"""Third-party harnesses: Claude Code and Codex session files read, counted once, priced."""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import pytest

from openprogram.models.usage import harnesses as oa
from openprogram.models.usage.harnesses import pricing
from openprogram.models.usage.harnesses.store import Store


@pytest.fixture
def homes(tmp_path, monkeypatch):
    claude = tmp_path / "claude"
    codex = tmp_path / "codex"
    (claude / "projects").mkdir(parents=True)
    (codex / "sessions").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    monkeypatch.setenv("CODEX_HOME", str(codex))
    monkeypatch.setattr(oa, "_store", Store(tmp_path / "state" / "harnesses.db"))
    monkeypatch.setattr(oa, "_last_scan", 0.0)
    monkeypatch.setattr(oa, "enabled", lambda: True)
    monkeypatch.setattr(importlib.import_module("openprogram.models.usage.harnesses.report"), "price", _fake_price)
    return claude, codex


_PRICES = {
    ("anthropic", "claude-test"): pricing.Price(
        base=pricing.Rates(1.0, 10.0, 0.1, 1.25),
        fast=pricing.Rates(2.0, 20.0, 0.2, 2.5), anthropic=True),
    ("openai", "gpt-test"): pricing.Price(
        base=pricing.Rates(2.0, 8.0, 0.2, 2.0), tier_above=1000, tier=pricing.Rates(4.0, 16.0, 0.4, 4.0)),
}


def _fake_price(provider, model):
    return _PRICES.get((provider, model))


def _ts(minute: int) -> str:
    return f"2099-01-01T00:{minute:02d}:00.000Z"


def _write(path: Path, records, *, newline=True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(r, separators=(",", ":")) for r in records) + ("\n" if newline else "")
    with open(path, "a") as fh:
        fh.write(text)


def _assistant(mid, rid, *, model="claude-test", minute=1, sid="s1", inp=10, out=5, read=100,
               write=40, one_hour=0, speed="standard", entry="cli"):
    return {
        "type": "assistant", "sessionId": sid, "requestId": rid, "timestamp": _ts(minute),
        "cwd": "/work/app", "gitBranch": "main", "entrypoint": entry,
        "message": {"id": mid, "model": model, "usage": {
            "input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": read,
            "cache_creation_input_tokens": write, "speed": speed,
            "cache_creation": {"ephemeral_1h_input_tokens": one_hour, "ephemeral_5m_input_tokens": write - one_hour},
        }},
    }


def _report():
    import time
    oa.scan()
    return oa.report(since=0, until=time.time() + 10**10)


def _agent(r, agent):
    return next(a for a in r["agents"] if a["agent"] == agent)


def test_claude_counts_each_message_once_across_blocks_and_copies(homes):
    claude, _ = homes
    folder = claude / "projects" / "-work-app"
    _write(folder / "s1.jsonl", [
        {"type": "user", "sessionId": "s1", "timestamp": _ts(0), "cwd": "/work/app",
         "message": {"role": "user", "content": "<command-name>/review</command-name><command-args>auth</command-args>"}},
        # One message, two content blocks: the usage repeats, then grows.
        _assistant("m1", "r1", out=3),
        _assistant("m1", "r1", out=5),
        _assistant("m2", "r2", model="<synthetic>"),
        {"type": "custom-title", "sessionId": "s1", "customTitle": "Fix login"},
    ])
    # A forked session copies m1 into a file of its own.
    _write(folder / "s2.jsonl", [_assistant("m1", "r1", sid="s2", out=5)])
    # A subagent's calls count toward its parent.
    _write(folder / "s1" / "subagents" / "agent-a.jsonl", [
        {**_assistant("m3", "r3", minute=2), "isSidechain": True}])
    _write(folder / "s1" / "subagents" / "workflows" / "w" / "journal.jsonl", [_assistant("m9", "r9")])

    r = _report()
    a = _agent(r, "claude_code")
    assert a["calls"] == 2
    assert a["output_tokens"] == 10
    s1 = next(s for s in r["sessions"] if s["id"] == "s1")
    assert s1["title"] == "Fix login" and s1["first_prompt"] == "/review auth"
    assert s1["calls"] == 2 and s1["client"] == "cli" and s1["branch"] == "main"
    assert s1["resume"] == "cd /work/app && claude --resume s1"


def test_claude_one_hour_writes_and_fast_calls_are_priced_apart(homes):
    claude, _ = homes
    _write(claude / "projects" / "p" / "s1.jsonl", [
        _assistant("m1", "r1", inp=1_000_000, out=0, read=0, write=1_000_000, one_hour=400_000),
        _assistant("m2", "r2", inp=1_000_000, out=0, read=0, write=0, speed="fast"),
    ])
    r = _report()
    # m1: 1M input at 1 + 600K 5-minute writes at 1.25 + 400K 1-hour writes at 2 × 1.
    # m2: 1M input at the fast price 2.
    assert _agent(r, "claude_code")["cost"] == pytest.approx(1 + 0.75 + 0.8 + 2)


def test_appended_lines_are_read_and_a_partial_line_waits(homes):
    claude, _ = homes
    path = claude / "projects" / "p" / "s1.jsonl"
    _write(path, [_assistant("m1", "r1")])
    _write(path, [_assistant("m2", "r2")], newline=False)
    assert _agent(_report(), "claude_code")["calls"] == 1
    with open(path, "a") as fh:
        fh.write("\n")
    _write(path, [_assistant("m3", "r3")])
    oa._last_scan = 0.0
    assert _agent(_report(), "claude_code")["calls"] == 3


def test_a_rewritten_file_is_read_again_from_the_start(homes):
    claude, _ = homes
    path = claude / "projects" / "p" / "s1.jsonl"
    _write(path, [_assistant("m1", "r1"), _assistant("m2", "r2")])
    _report()
    path.write_text(json.dumps(_assistant("m4", "r4")) + "\n")
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    # Calls already counted stay; the new content is read from byte 0.
    assert _agent(_report(), "claude_code")["calls"] == 3


def _codex_file(codex: Path, name: str, lines) -> Path:
    path = codex / "sessions" / "2099" / "01" / "01" / name
    _write(path, lines)
    return path


def _meta(tid, originator="codex_cli_rs"):
    return {"type": "session_meta", "timestamp": _ts(0),
            "payload": {"id": tid, "cwd": "/work/api", "originator": originator, "git": {"branch": "dev"}}}


def _turn(model="gpt-test"):
    return {"type": "turn_context", "timestamp": _ts(0), "payload": {"model": model}}


def _usage(inp, cached, out):
    return {"input_tokens": inp, "cached_input_tokens": cached, "cache_write_input_tokens": 0,
            "output_tokens": out, "total_tokens": inp + out}


def _count(minute, last, total, limits=None):
    payload = {"type": "token_count", "info": {"last_token_usage": last,
                                                "total_token_usage": {"total_tokens": total}}}
    if limits:
        payload["rate_limits"] = limits
    return {"type": "event_msg", "timestamp": _ts(minute), "payload": payload}


def test_codex_records_win_over_token_counts(homes):
    _, codex = homes
    _codex_file(codex, "rollout-2099-01-01T00-00-00-t1.jsonl", [
        _meta("t1"), _turn(),
        {"type": "response_item", "timestamp": _ts(0), "payload": {
            "type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "<environment_context>x</environment_context>"}]}},
        {"type": "response_item", "timestamp": _ts(0), "payload": {
            "type": "message", "role": "user", "content": [{"type": "input_text", "text": "Add tests"}]}},
        {"type": "token_usage_record", "timestamp": _ts(1),
         "payload": {"response_id": "resp1", "usage": _usage(500, 300, 10)}},
        _count(1, _usage(500, 300, 10), 510,
               {"primary": {"used_percent": 12.0, "window_minutes": 10080, "resets_at": 4070908800},
                "credits": {"balance": "41.5", "unlimited": False}, "plan_type": "pro"}),
    ])
    (codex / "session_index.jsonl").write_text(
        json.dumps({"id": "t1", "thread_name": "old"}) + "\n" + json.dumps({"id": "t1", "thread_name": "API tests"}) + "\n")
    r = _report()
    a = _agent(r, "codex")
    assert a["calls"] == 1
    assert (a["input_tokens"], a["cache_read_tokens"], a["output_tokens"]) == (200, 300, 10)
    s = r["sessions"][0]
    assert (s["title"], s["first_prompt"], s["client"], s["branch"]) == ("API tests", "Add tests", "codex_cli_rs", "dev")
    assert s["resume"] == "cd /work/api && codex resume t1"
    q = r["quota"][0]
    assert q["windows"][0]["used_percent"] == 12.0 and q["credits"] == 41.5 and q["plan"] == "pro"


def test_old_codex_counts_skip_repeats_and_a_fork_counts_copied_history_once(homes):
    _, codex = homes
    history = [_turn(), _count(1, _usage(100, 0, 10), 110), _count(1, _usage(100, 0, 10), 110),
               _count(2, _usage(0, 0, 0), 110), _count(3, _usage(200, 100, 20), 330)]
    _codex_file(codex, "rollout-2099-01-01T00-00-00-t1.jsonl", [_meta("t1"), *history])
    _codex_file(codex, "rollout-2099-01-01T00-05-00-t2.jsonl", [_meta("t2"), *history])
    a = _agent(_report(), "codex")
    assert a["calls"] == 2
    assert a["output_tokens"] == 30


def test_codex_zstd_archive_is_read(homes):
    import zstandard

    _, codex = homes
    raw = "\n".join(json.dumps(x) for x in [
        _meta("t3"), _turn(),
        {"type": "token_usage_record", "timestamp": _ts(1), "payload": {"response_id": "z1", "usage": _usage(2000, 0, 0)}},
    ]) + "\n"
    path = codex / "archived_sessions" / "rollout-2099-01-01T00-00-00-t3.jsonl.zst"
    path.parent.mkdir(parents=True)
    path.write_bytes(zstandard.ZstdCompressor().compress(raw.encode()))
    a = _agent(_report(), "codex")
    assert a["calls"] == 1
    # The prompt passed the model's 1000-token tier: priced at 4 per million.
    assert a["cost"] == pytest.approx(2000 * 4 / 1_000_000)


def test_an_unknown_model_counts_tokens_but_not_cost(homes):
    claude, _ = homes
    _write(claude / "projects" / "p" / "s1.jsonl", [_assistant("m1", "r1", model="claude-unknown")])
    a = _agent(_report(), "claude_code")
    assert a["calls"] == 1 and a["unpriced_calls"] == 1 and a["cost"] == 0
    assert a["total_tokens"] == 155


def test_one_hour_cache_price_is_anthropic_only():
    rates = pricing.Rates(1.0, 1.0, 1.0, 1.25)
    tokens = dict(input=0, cache_read=0, cache_write=1_000_000, cache_write_1h=1_000_000, output=0)
    assert pricing.Price(base=rates, anthropic=True).cost("", False, *tokens.values()) == pytest.approx(2.0)
    assert pricing.Price(base=rates, anthropic=False).cost("", False, *tokens.values()) == pytest.approx(1.25)

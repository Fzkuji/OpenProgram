"""The always-on weekly health report: reads one state directory, changes nothing.

Conversation state and run rows live inside each conversation's log, so the
log readers are used; they only read. No store object is built, because a store
may create what it finds missing. Runs through ``always_on_weekly.sh``.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass, field
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

from openprogram.state.store.project.layout import DEFAULT_PROJECT_ID, memory_dir
from openprogram.state.store.session.session_log import SessionLog
from openprogram.state.store.session.session_state_file import read_files

WEEK_SECONDS = 7 * 24 * 3600
HOUR_SECONDS = 3600
DAY_SECONDS = 24 * 3600
STATE_NAME = "always_on.json"
REPORTS_NAME = "always_on_reports.jsonl"
WAKE_LOG_NAME = "always_on_wake_log.jsonl"
FAILED_OUTCOMES = frozenset({"failed", "interrupted"})
REPORT_STATE_LIMIT_BYTES = 1024 * 1024
CHECK_IN_SPEAKER = "runtime/always_on"
REQUESTED = "execution.restart.requested"
SETTLED = "execution.restart.settled"
PAUSED = "paused"


@dataclass
class Conversation:
    session_id: str
    agent_id: str
    wakes: list[dict[str, Any]]
    reports: list[dict[str, Any]]
    report_bytes: int


@dataclass
class RunState:
    open_intents: list[str] = field(default_factory=list)
    paused_without_intent: list[str] = field(default_factory=list)
    longest_check_in: float = 0.0
    longest_scheduled: float = 0.0


def _rows(text: str) -> list[dict[str, Any]]:
    rows = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _json_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def conversation_directories(state_dir: Path) -> list[Path]:
    return sorted(path for path in state_dir.glob("projects/*/chats/*") if path.is_dir())


def _read_conversation(directory: Path, files: dict[str, str]) -> Conversation:
    reports_text = files.get(REPORTS_NAME)
    if reports_text is None:
        try:
            reports_text = (directory / REPORTS_NAME).read_text(encoding="utf-8")
        except OSError:
            reports_text = ""
    try:
        wake_text = (directory / WAKE_LOG_NAME).read_text(encoding="utf-8")
    except OSError:
        wake_text = ""
    agent_id = str(SessionLog(directory).read_meta().get("agent_id") or "unknown")
    return Conversation(directory.name, agent_id, _rows(wake_text), _rows(reports_text),
                        len(reports_text.encode("utf-8")))


def _fold_run_rows(batches: list[list[dict]]) -> dict[str, dict[Any, dict]]:
    tables: dict[str, dict[Any, dict]] = defaultdict(dict)
    keys = {"executions": "execution_id", "execution_events": "sequence",
            "execution_agent_turn_inputs": "execution_id"}
    for batch in batches:
        for line in batch:
            key = keys.get(line.get("t"))
            if key is None:
                continue
            if "gone" in line:
                tables[line["t"]].pop(line["gone"][0], None)
            elif isinstance(line.get("r"), dict):
                tables[line["t"]][line["r"].get(key)] = line["r"]
    return tables


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = json.loads(row.get("payload_json") or "{}")
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _add_run_state(result: RunState, directory: Path, now: float) -> None:
    tables = _fold_run_rows(SessionLog(directory).read_run_rows())
    by_execution: dict[str, list[dict]] = defaultdict(list)
    for event in tables["execution_events"].values():
        by_execution[event.get("execution_id")].append(event)
    for execution_id, execution in tables["executions"].items():
        events = by_execution.get(execution_id, [])
        settled = {_payload(e).get("request_sequence") for e in events if e.get("kind") == SETTLED}
        has_open = any(e.get("kind") == REQUESTED and e.get("sequence") not in settled for e in events)
        input_row = tables["execution_agent_turn_inputs"].get(execution_id)
        request = (_payload(input_row).get("request") or {}) if input_row else {}
        is_check_in = request.get("speaker_id") == CHECK_IN_SPEAKER
        is_scheduled = bool(request.get("scheduled_task"))
        if has_open:
            result.open_intents.append(execution_id)
        elif execution.get("status") == PAUSED and (is_check_in or is_scheduled):
            # A chat's own turn stays paused for the user to continue by hand.
            result.paused_without_intent.append(execution_id)
        finished = execution.get("terminal_at")
        started = execution.get("created_at")
        if not isinstance(finished, (int, float)) or not isinstance(started, (int, float)):
            continue
        if finished < now - WEEK_SECONDS:
            continue
        duration = finished - started
        if is_scheduled:
            result.longest_scheduled = max(result.longest_scheduled, duration)
        elif is_check_in:
            result.longest_check_in = max(result.longest_check_in, duration)


def _peer_lines(peers_path: Path) -> list[str]:
    lines = ["", "## Peers"]
    for peer in json.loads(peers_path.read_text(encoding="utf-8"))["peers"]:
        try:
            answer = subprocess.run(["git", "ls-remote", peer["repository"], "HEAD"], capture_output=True,
                                    text=True, timeout=60, check=True).stdout
            head = answer.split()[0]
        except (OSError, IndexError, subprocess.SubprocessError):
            lines.append(f"- {peer['name']}: could not be read")
            continue
        state = "unchanged" if head.startswith(peer["baseline"]) else f"changed, now {head[:9]}"
        lines.append(f"- {peer['name']}: baseline {peer['baseline']}, {state}")
    return lines


def build_report(state_dir: Path, now: float, peers_path: Path | None = None) -> str:
    """The report text. The last line is the verdict."""
    conversations = []
    runs = RunState()
    for directory in conversation_directories(state_dir):
        _add_run_state(runs, directory, now)
        files = read_files(directory)
        if STATE_NAME in files:
            conversations.append(_read_conversation(directory, files))

    since = now - WEEK_SECONDS
    outcomes: dict[str, Counter] = defaultdict(Counter)
    spend: dict[str, list[float]] = defaultdict(list)
    prompt_tokens: dict[str, int] = defaultdict(int)
    stuck_deliveries = []
    reported = quiet = 0
    for conversation in conversations:
        for wake in conversation.wakes:
            if not isinstance(wake.get("at"), (int, float)) or wake["at"] < since:
                continue
            outcome = str(wake.get("outcome") or "unknown")
            outcomes[conversation.agent_id][outcome] += 1
            reported += outcome == "reported"
            quiet += outcome == "nothing_new"
            cost = wake.get("cost") or {}
            if cost.get("usd"):
                spend[conversation.agent_id].append(float(cost["usd"]))
            prompt_tokens[conversation.agent_id] = max(
                prompt_tokens[conversation.agent_id], int(cost.get("input_tokens") or 0))
        for report in conversation.reports:
            for delivery in report.get("deliveries") or []:
                if delivery.get("status") == "sending" and (report.get("at") or now) < now - HOUR_SECONDS:
                    stuck_deliveries.append(f"{conversation.session_id}/{report.get('id')}")

    attention: list[str] = []
    lines = ["# Always-on weekly health", "", "## Firings in the last 7 days"]
    for agent_id, counts in sorted(outcomes.items()):
        lines.append(f"- {agent_id}: " + ", ".join(f"{name} {count}" for name, count in sorted(counts.items())))
        failed = sum(counts[name] for name in FAILED_OUTCOMES)
        if failed:
            attention.append(f"{agent_id} had {failed} failed or cut-off wakes")
    if not outcomes:
        lines.append("- none")

    tasks = _json_file(state_dir / "scheduler" / "task-runs.json")
    task_counts: Counter = Counter(
        str(run.get("status")) for entry in tasks.values() if isinstance(entry, dict)
        for run in entry.get("runs") or [] if (run.get("at") or 0) >= since)
    lines += ["", "## Scheduled tasks in the last 7 days",
              "- " + (", ".join(f"{name} {count}" for name, count in sorted(task_counts.items())) or "none")]
    if task_counts["failed"]:
        attention.append(f"{task_counts['failed']} scheduled task runs failed")

    lines += ["", "## Cost"]
    for agent_id in sorted(outcomes):
        costs = spend.get(agent_id, [])
        average = sum(costs) / len(costs) if costs else 0.0
        state = _json_file(state_dir / "agents" / agent_id / "always_on" / "state.json")
        today = (state.get("spend") or {}).get("usd", 0)
        lines.append(f"- {agent_id}: ${sum(costs):.4f} over {len(costs)} check-ins, ${average:.4f} each; "
                     f"latest day ${float(today):.4f}; budget stops {outcomes[agent_id]['budget']}; "
                     f"largest prompt {prompt_tokens[agent_id]} tokens")
    if not outcomes:
        lines.append("- none")

    lines += ["", "## Must be zero",
              f"- Unsettled restart intents: {len(runs.open_intents)}",
              f"- Check-ins and scheduled runs paused with no intent: {len(runs.paused_without_intent)}",
              f"- Deliveries sending for over an hour: {len(stuck_deliveries)}"]
    if runs.open_intents:
        attention.append(f"{len(runs.open_intents)} unsettled restart intents: " + ", ".join(runs.open_intents[:5]))
    if runs.paused_without_intent:
        attention.append(f"{len(runs.paused_without_intent)} automatic runs paused with no intent: "
                         + ", ".join(runs.paused_without_intent[:5]))
    if stuck_deliveries:
        attention.append(f"{len(stuck_deliveries)} stuck deliveries: " + ", ".join(stuck_deliveries[:5]))

    lines += ["", "## Longest runs in the last 7 days",
              f"- Scheduled run: {runs.longest_scheduled:.0f} s",
              f"- Check-in: {runs.longest_check_in:.0f} s"]

    looks = reported + quiet
    lines += ["", "## Reports against nothing new", f"- reported {reported}, nothing new {quiet}"]
    if looks and reported * 2 > looks:
        attention.append(f"reports were {reported} of {looks} looks, which suggests noise")

    handoffs = _json_file(state_dir / "always_on" / "handoffs.json")
    old = [message_id for message_id, entry in handoffs.items() if isinstance(entry, dict)
           and entry.get("phase") != "done" and (entry.get("at") or now) < now - DAY_SECONDS]
    lines += ["", "## Handoffs", f"- Unfinished for over 24 hours: {len(old)}"]
    if old:
        attention.append(f"{len(old)} handoffs unfinished for over 24 hours")

    heavy = [c.session_id for c in conversations if c.report_bytes > REPORT_STATE_LIMIT_BYTES]
    memory_bytes = sum(path.stat().st_size for path in memory_dir(DEFAULT_PROJECT_ID, state_dir).rglob("*") if path.is_file())
    lines += ["", "## Watch list",
              f"- One conversation's report state above 1 MB: {len(heavy)}",
              f"- Memory size: {memory_bytes} bytes; compare with prompt tokens above",
              "- Read by hand: a user asked for connected tools in check-ins, a check-in lacked one, "
              "a user reported a missed finding in an idle conversation"]
    if heavy:
        attention.append("the one-document-per-report trigger is met: " + ", ".join(heavy[:5]))

    if peers_path is not None:
        lines += _peer_lines(peers_path)

    lines += ["", "## Verdict"]
    lines += [f"- {item}" for item in attention]
    lines.append(f"NEEDS ATTENTION: {len(attention)} item(s)" if attention else "HEALTHY")
    return "\n".join(lines)


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=Path.home() / ".openprogram")
    parser.add_argument("--peers", type=Path, help="Peer baseline file; leave out to skip the network read")
    options = parser.parse_args(arguments)
    if not options.state_dir.is_dir():
        print(f"No state directory at {options.state_dir}", file=sys.stderr)
        return 2
    print(build_report(options.state_dir, time.time(), options.peers))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

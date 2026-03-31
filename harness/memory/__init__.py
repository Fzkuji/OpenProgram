"""
Memory — persistent execution log for Agentic Programming.

Like a program's runtime log, Memory records everything that happens:
    - Every Function call (input, output, timing, status)
    - Every Programmer decision (reasoning, action)
    - Every Session message (text, images, errors)
    - The full call graph (who called whom, in what order)

Storage format:
    run_<timestamp>/
    ├── run.jsonl          # Structured event log (one JSON per line)
    ├── run.md             # Human-readable summary
    └── media/             # Images and other binary files
        ├── 001_observe_input.png
        └── 002_observe_output.png

Design principles:
    1. Append-only — never modify past entries
    2. Structured + readable — JSONL for machines, Markdown for humans
    3. Media is referenced by path — images saved to disk, linked in log
    4. Hierarchical — events have parent IDs forming a call tree
    5. Replayable — enough info to understand exactly what happened
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Any
import uuid


@dataclass
class Event:
    """A single event in the execution log."""

    type: str                           # "function_call", "function_return", "decision",
                                        # "message_sent", "message_received", "error",
                                        # "run_start", "run_end", "media"
    timestamp: str = ""                 # ISO format
    id: str = ""                        # unique event ID
    parent_id: Optional[str] = None     # parent event ID (call tree)
    function_name: Optional[str] = None
    session_type: Optional[str] = None  # "AnthropicSession", "ClaudeCodeSession", etc.
    scope: Optional[str] = None         # Scope description
    data: dict = field(default_factory=dict)  # type-specific payload
    duration_ms: Optional[float] = None
    status: Optional[str] = None        # "success", "error", "retry"
    media_paths: list[str] = field(default_factory=list)  # relative paths to saved media

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()
        if not self.id:
            self.id = uuid.uuid4().hex[:12]


class Memory:
    """
    Persistent execution log.

    Usage:
        memory = Memory(base_dir="./logs")
        run_id = memory.start_run(task="Click the login button")

        # Log events as they happen
        memory.log_function_call("observe", params={"task": "..."}, scope="isolated")
        memory.log_message_sent("observe", message="Take a screenshot...")
        memory.log_message_received("observe", reply="...")
        media_path = memory.save_media("screenshot.png", source_path="/tmp/screen.png")
        memory.log_function_return("observe", result={...}, media=[media_path])

        memory.log_decision(action="call", reasoning="...", function_name="act")

        memory.end_run(status="success")

        # Later: read back
        events = memory.load_run(run_id)
        summary = memory.get_summary(run_id)
    """

    def __init__(self, base_dir: str = "./logs"):
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)

        self._run_dir: Optional[Path] = None
        self._run_id: Optional[str] = None
        self._jsonl_file = None
        self._events: list[Event] = []
        self._event_stack: list[str] = []  # parent ID stack
        self._media_counter = 0
        self._run_start_time: Optional[float] = None

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def start_run(self, task: str, metadata: dict = None) -> str:
        """Start a new run. Returns run_id."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._run_id = f"run_{ts}_{uuid.uuid4().hex[:6]}"
        self._run_dir = self._base_dir / self._run_id
        self._run_dir.mkdir(parents=True)
        (self._run_dir / "media").mkdir()

        self._events = []
        self._event_stack = []
        self._media_counter = 0
        self._run_start_time = time.time()

        # Open JSONL file
        self._jsonl_file = open(self._run_dir / "run.jsonl", "a")

        event = Event(
            type="run_start",
            data={"task": task, **(metadata or {})},
        )
        self._write_event(event)

        return self._run_id

    def end_run(self, status: str = "success", summary: str = None):
        """End the current run and generate summary."""
        duration = (time.time() - self._run_start_time) * 1000 if self._run_start_time else 0

        event = Event(
            type="run_end",
            status=status,
            duration_ms=duration,
            data={"summary": summary} if summary else {},
        )
        self._write_event(event)

        # Close JSONL
        if self._jsonl_file:
            self._jsonl_file.close()
            self._jsonl_file = None

        # Generate human-readable summary
        self._generate_markdown_summary()

        run_id = self._run_id
        self._run_id = None
        self._run_dir = None
        return run_id

    # ------------------------------------------------------------------
    # Event logging
    # ------------------------------------------------------------------

    def log_function_call(
        self,
        function_name: str,
        params: dict = None,
        scope: str = None,
        session_type: str = None,
    ) -> str:
        """Log a Function call. Returns event ID (use as parent for nested events)."""
        event = Event(
            type="function_call",
            function_name=function_name,
            scope=scope,
            session_type=session_type,
            parent_id=self._current_parent,
            data={"params": params or {}},
        )
        self._write_event(event)
        self._event_stack.append(event.id)
        return event.id

    def log_function_return(
        self,
        function_name: str,
        result: Any = None,
        status: str = "success",
        error: str = None,
        duration_ms: float = None,
        media: list[str] = None,
    ):
        """Log a Function return."""
        # Pop from stack
        if self._event_stack:
            self._event_stack.pop()

        data = {}
        if result is not None:
            data["result"] = result if isinstance(result, dict) else str(result)
        if error:
            data["error"] = error

        event = Event(
            type="function_return",
            function_name=function_name,
            status=status,
            duration_ms=duration_ms,
            parent_id=self._current_parent,
            data=data,
            media_paths=media or [],
        )
        self._write_event(event)

    def log_decision(
        self,
        action: str,
        reasoning: str = None,
        function_name: str = None,
        data: dict = None,
    ):
        """Log a Programmer decision."""
        event = Event(
            type="decision",
            function_name=function_name,
            parent_id=self._current_parent,
            data={
                "action": action,
                "reasoning": reasoning or "",
                **(data or {}),
            },
        )
        self._write_event(event)

    def log_message_sent(
        self,
        function_name: str,
        message: str = None,
        media: list[str] = None,
    ):
        """Log a message sent to a Session."""
        data = {}
        if message:
            # Truncate very long messages for readability
            data["message"] = message[:2000] + ("..." if len(message) > 2000 else "")
            data["message_length"] = len(message)

        event = Event(
            type="message_sent",
            function_name=function_name,
            parent_id=self._current_parent,
            data=data,
            media_paths=media or [],
        )
        self._write_event(event)

    def log_message_received(
        self,
        function_name: str,
        reply: str = None,
        media: list[str] = None,
    ):
        """Log a reply received from a Session."""
        data = {}
        if reply:
            data["reply"] = reply[:2000] + ("..." if len(reply) > 2000 else "")
            data["reply_length"] = len(reply)

        event = Event(
            type="message_received",
            function_name=function_name,
            parent_id=self._current_parent,
            data=data,
            media_paths=media or [],
        )
        self._write_event(event)

    def log_error(self, error: str, function_name: str = None):
        """Log an error."""
        event = Event(
            type="error",
            function_name=function_name,
            status="error",
            parent_id=self._current_parent,
            data={"error": error},
        )
        self._write_event(event)

    # ------------------------------------------------------------------
    # Media handling
    # ------------------------------------------------------------------

    def save_media(
        self,
        filename: str,
        source_path: str = None,
        data: bytes = None,
    ) -> str:
        """
        Save a media file (image, etc.) to the run's media directory.

        Args:
            filename:    Target filename (e.g. "screenshot.png")
            source_path: Copy from this path
            data:        Or save these bytes directly

        Returns:
            Relative path within the run directory (e.g. "media/001_screenshot.png")
        """
        if not self._run_dir:
            raise RuntimeError("No active run. Call start_run() first.")

        self._media_counter += 1
        prefix = f"{self._media_counter:03d}_"
        target_name = prefix + filename
        target_path = self._run_dir / "media" / target_name

        if source_path:
            shutil.copy2(source_path, target_path)
        elif data:
            target_path.write_bytes(data)
        else:
            raise ValueError("Provide source_path or data")

        relative = f"media/{target_name}"

        # Also log the media save event
        event = Event(
            type="media",
            parent_id=self._current_parent,
            data={"filename": filename, "path": relative, "size_bytes": target_path.stat().st_size},
            media_paths=[relative],
        )
        self._write_event(event)

        return relative

    # ------------------------------------------------------------------
    # Reading logs
    # ------------------------------------------------------------------

    def load_run(self, run_id: str) -> list[Event]:
        """Load all events from a previous run."""
        jsonl_path = self._base_dir / run_id / "run.jsonl"
        if not jsonl_path.exists():
            raise FileNotFoundError(f"Run not found: {run_id}")

        events = []
        with open(jsonl_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    data = json.loads(line)
                    events.append(Event(**data))
        return events

    def get_summary(self, run_id: str) -> str:
        """Get the Markdown summary of a run."""
        md_path = self._base_dir / run_id / "run.md"
        if md_path.exists():
            return md_path.read_text()
        return ""

    def list_runs(self) -> list[dict]:
        """List all runs with basic info."""
        runs = []
        for d in sorted(self._base_dir.iterdir()):
            if d.is_dir() and d.name.startswith("run_"):
                jsonl = d / "run.jsonl"
                if jsonl.exists():
                    # Read first and last events
                    with open(jsonl) as f:
                        lines = f.readlines()
                    first = json.loads(lines[0]) if lines else {}
                    last = json.loads(lines[-1]) if lines else {}
                    runs.append({
                        "run_id": d.name,
                        "task": first.get("data", {}).get("task", ""),
                        "status": last.get("status", "unknown"),
                        "started": first.get("timestamp", ""),
                        "ended": last.get("timestamp", ""),
                        "event_count": len(lines),
                    })
        return runs

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @property
    def _current_parent(self) -> Optional[str]:
        return self._event_stack[-1] if self._event_stack else None

    def _write_event(self, event: Event):
        """Write event to JSONL and keep in memory."""
        self._events.append(event)
        if self._jsonl_file:
            self._jsonl_file.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")
            self._jsonl_file.flush()

    def _generate_markdown_summary(self):
        """Generate a human-readable Markdown summary of the run."""
        if not self._run_dir or not self._events:
            return

        lines = []
        run_start = next((e for e in self._events if e.type == "run_start"), None)
        run_end = next((e for e in self._events if e.type == "run_end"), None)

        # Header
        task = run_start.data.get("task", "Unknown") if run_start else "Unknown"
        lines.append(f"# Run: {self._run_id}")
        lines.append(f"")
        lines.append(f"**Task:** {task}")
        lines.append(f"**Started:** {run_start.timestamp if run_start else 'unknown'}")
        lines.append(f"**Ended:** {run_end.timestamp if run_end else 'unknown'}")
        if run_end:
            lines.append(f"**Status:** {run_end.status}")
            if run_end.duration_ms:
                lines.append(f"**Duration:** {run_end.duration_ms:.0f}ms")
        lines.append("")
        lines.append("---")
        lines.append("")

        # Events
        lines.append("## Execution Log")
        lines.append("")

        for event in self._events:
            if event.type == "run_start" or event.type == "run_end":
                continue

            ts = event.timestamp.split("T")[-1][:8] if "T" in event.timestamp else event.timestamp
            indent = "  " if event.parent_id else ""

            if event.type == "function_call":
                params_str = json.dumps(event.data.get("params", {}), ensure_ascii=False)
                if len(params_str) > 200:
                    params_str = params_str[:200] + "..."
                scope_str = f" [{event.scope}]" if event.scope else ""
                session_str = f" via {event.session_type}" if event.session_type else ""
                lines.append(f"{indent}### `{event.function_name}()`{scope_str}{session_str}")
                lines.append(f"{indent}*{ts}*")
                lines.append(f"{indent}**Input:** `{params_str}`")
                lines.append("")

            elif event.type == "function_return":
                status_icon = "✓" if event.status == "success" else "✗"
                duration_str = f" ({event.duration_ms:.0f}ms)" if event.duration_ms else ""
                lines.append(f"{indent}{status_icon} **Return**{duration_str}")
                result = event.data.get("result", "")
                if result:
                    result_str = json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else str(result)
                    if len(result_str) > 300:
                        result_str = result_str[:300] + "..."
                    lines.append(f"{indent}**Output:** `{result_str}`")
                if event.data.get("error"):
                    lines.append(f"{indent}**Error:** {event.data['error']}")
                # Media links
                for mp in event.media_paths:
                    lines.append(f"{indent}📎 [{mp}]({mp})")
                lines.append("")

            elif event.type == "decision":
                action = event.data.get("action", "?")
                reasoning = event.data.get("reasoning", "")
                fn = event.function_name or ""
                lines.append(f"{indent}💭 **Decision:** {action}")
                if fn:
                    lines.append(f"{indent}**Target:** `{fn}`")
                if reasoning:
                    lines.append(f"{indent}**Reasoning:** {reasoning[:200]}")
                lines.append("")

            elif event.type == "message_sent":
                msg = event.data.get("message", "")
                length = event.data.get("message_length", len(msg))
                lines.append(f"{indent}📤 **Sent** to `{event.function_name}` ({length} chars)")
                for mp in event.media_paths:
                    lines.append(f"{indent}📎 [{mp}]({mp})")
                lines.append("")

            elif event.type == "message_received":
                reply = event.data.get("reply", "")
                length = event.data.get("reply_length", len(reply))
                lines.append(f"{indent}📥 **Received** from `{event.function_name}` ({length} chars)")
                for mp in event.media_paths:
                    lines.append(f"{indent}📎 [{mp}]({mp})")
                lines.append("")

            elif event.type == "error":
                lines.append(f"{indent}❌ **Error**")
                if event.function_name:
                    lines.append(f"{indent}**In:** `{event.function_name}`")
                lines.append(f"{indent}**Message:** {event.data.get('error', '')}")
                lines.append("")

            elif event.type == "media":
                path = event.data.get("path", "")
                size = event.data.get("size_bytes", 0)
                lines.append(f"{indent}📎 Saved: [{path}]({path}) ({size} bytes)")
                lines.append("")

        # Write
        md_path = self._run_dir / "run.md"
        md_path.write_text("\n".join(lines))

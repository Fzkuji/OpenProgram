"""
Claude Code CLI provider — routes LLM calls through the Claude Code CLI.

Uses Claude Code in SDK/agent mode (stream-json) which is covered by
Claude Code subscription. No API key needed — uses the logged-in session.

Architecture:
  A single long-running `claude` process is kept alive for the entire runtime.
  Messages are sent via stdin (stream-json format) and responses read from
  stdout. The agent runs in full mode — it can use tools, edit files, and
  execute commands. This eliminates process startup overhead (~2-3s per call)
  and enables natural KV cache reuse across turns.

Supports:
- Text content blocks
- Image content blocks (base64 encoded via stream-json)
- Session continuity (single persistent process)
- Full agent execution (tool use, file editing, bash commands)

Unsupported (with warnings):
- Audio content blocks (Claude CLI does not support audio input)
- Video content blocks (Claude CLI does not support video input)
- File/PDF content blocks (Claude CLI does not support document input)

Usage:
    from openprogram.legacy_providers.claude_code import ClaudeCodeRuntime

    runtime = ClaudeCodeRuntime(model="sonnet")

    # Reasoning mode (exec)
    @agentic_function
    def observe(task):
        return runtime.exec(content=[
            {"type": "text", "text": f"Find: {task}"},
            {"type": "image", "path": "screenshot.png"},
        ])

    # Execution mode (execute)
    result = runtime.execute("Create a file called hello.py with a hello world script")
    # Returns "DONE" or "ERROR: ..."
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
import warnings
from typing import Optional

from openprogram.agentic_programming.runtime import Runtime


class ClaudeCodeRuntime(Runtime):
    """
    Runtime that routes LLM calls through a persistent Claude Code CLI process.

    Runs Claude Code in SDK/agent mode (not -p print mode). The agent has
    full capabilities: tool use, file editing, bash commands, etc.

    A single process is started on first call and kept alive. All subsequent
    calls reuse the same process via stdin/stdout streaming, eliminating
    the ~2-3s startup overhead per call.

    Requires `claude` CLI to be installed and logged in.
    Uses Claude Code subscription (no separate API key needed).

    Args:
        model:      Model to use (default: "claude-sonnet-4-6"). Passed to --model flag.
                    Must match an id in claude_models.json (see claude_models.py).
        timeout:    Max seconds per LLM call (default: 600). In SDK agent mode
                    the agent may use tools (file editing, bash), so calls
                    take longer than pure text responses.
        cli_path:   Path to claude CLI binary (auto-detected if not specified).
        session_id: Kept for API compat. Ignored (persistent process manages
                    its own session internally).
        max_turns_per_process: Restart process after this many turns to
                    prevent context window overflow (default: 20).
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        timeout: int = 600,
        cli_path: str = None,
        session_id: str = "auto",
        max_turns_per_process: int = 100,
        compact_every: int = 0,
        tools: str = None,
        compact_ratio: float = 0.8,
        compact_cap_tokens: int = 500_000,
    ):
        super().__init__(model=model)
        self.timeout = timeout
        self.cli_path = cli_path or shutil.which("claude")
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._turn_count = 0
        self._compact_every = compact_every
        self._max_turns = max_turns_per_process
        self._tools = tools  # e.g. "" for no tools, "Bash" for bash only
        # Persistent process manages its own context — skip summarize()
        self.has_session = session_id is not None
        self.last_thread_id = None  # for external session reuse

        # Context-window-aware compact trigger. CLI reports `contextWindow` +
        # token usage per turn in the result event's `modelUsage` field, so we
        # can apply a percent-based rule: for ≤500K windows compact at ratio
        # (default 80%), for >500K windows cap the trigger at compact_cap_tokens
        # (default 500K) to avoid wasting huge contexts.
        self._compact_ratio = compact_ratio
        self._compact_cap_tokens = compact_cap_tokens
        self._last_context_tokens = 0
        self._context_window_tokens: Optional[int] = None
        self._resolved_model_id: Optional[str] = None  # what CLI actually picked

        if self.cli_path is None:
            raise FileNotFoundError(
                "Claude Code CLI not found. Install it first:\n"
                "  npm install -g @anthropic-ai/claude-code\n"
                "Then log in:\n"
                "  claude login"
            )

    def list_models(self) -> list[str]:
        """Return selectable Claude Code CLI model IDs.

        Reads from the curated registry at `claude_models.json`. The
        registry is maintained by `refresh_claude_models` (agentic
        function) and auto-restored via `doctor()` if corrupted — see
        `openprogram/providers/claude_models.py` for the full strategy.
        """
        from openprogram.legacy_providers.claude_models import list_model_ids
        return list_model_ids()

    def _ensure_process(self):
        """Start the persistent claude process if not already running.

        Also restarts the process every max_turns_per_process turns to
        prevent context window overflow from accumulated conversation
        history (especially with images).
        """
        if self._proc is not None and self._proc.poll() is None:
            if self._turn_count < self._max_turns:
                return  # Still alive and under turn limit
            # Turn limit reached — restart process to clear context
            self._restart_process()

        cmd = [
            self.cli_path,
            "--permission-mode", "bypassPermissions",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose",
        ]

        # Always pass --model when set. Previously this skipped on "sonnet"
        # assuming it was the CLI default, but the CLI default changed to
        # Opus 4.7 [1m] — so skipping silently gave users Opus when they
        # asked for Sonnet.
        if self.model:
            cmd.extend(["--model", self.model])

        # Thinking effort — written via --settings rather than --effort because
        # the CLI's --effort flag argparse whitelist excludes "auto" (CLI bug),
        # while the runtime + /effort slash command + settings all accept it.
        # `defaultEffortLevel` is the canonical settings key (matches what
        # ~/.claude/settings.json uses and what /effort writes to).
        effort = getattr(self, '_thinking_effort', None)
        if effort:
            effort_map = {
                "none": "low",
                "low": "low",
                "medium": "medium",
                "high": "high",
                "xhigh": "xhigh",
                "max": "max",
                "auto": "auto",
            }
            mapped = effort_map.get(effort, effort)
            cmd.extend(["--settings", json.dumps({"defaultEffortLevel": mapped})])

        if self._tools is not None:
            cmd.extend(["--tools", self._tools])

        # Remove ANTHROPIC_API_KEY so CLI uses subscription, not API credits
        env = os.environ.copy()
        env.pop("ANTHROPIC_API_KEY", None)

        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # Line buffered
            env=env,
        )
        self._turn_count = 0
        # Fresh process → fresh context; clear the token counters. Keep the
        # cached context_window (discovered from a prior turn) since the model
        # hasn't changed.
        self._last_context_tokens = 0

        # Persistent stdout reader thread + queue (avoids race conditions
        # from creating multiple readline threads). Each call to
        # _read_line_with_timeout previously spawned a new thread that
        # blocked on readline(); old threads kept blocking even after timeout,
        # causing multiple threads to race on the same pipe and lose data.
        import queue
        self._stdout_queue = queue.Queue()
        self._stdout_thread = threading.Thread(
            target=self._read_stdout_loop, daemon=True
        )
        self._stdout_thread.start()

        # Drain stderr in background to prevent buffer deadlock.
        # Without this, complex tasks that produce lots of stderr output
        # (e.g., multi-tool Bash calls) fill the 64KB pipe buffer, causing
        # the process to block on stderr.write() which also blocks stdout.
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, daemon=True
        )
        self._stderr_thread.start()

    def _call(self, content: list[dict], model: str = "sonnet", response_format: dict = None) -> str:
        """Send a message to the persistent claude process and read the response.

        Unsupported block types (audio, video, file) emit warnings and are skipped.
        """
        # Filter unsupported block types
        filtered_content = []
        for block in content:
            btype = block.get("type", "text")
            if btype == "audio":
                warnings.warn(
                    "ClaudeCodeRuntime does not support audio content blocks. "
                    "Audio block will be skipped. Use AnthropicRuntime API directly for full multimodal support.",
                    UserWarning,
                    stacklevel=3,
                )
            elif btype == "video":
                warnings.warn(
                    "ClaudeCodeRuntime does not support video content blocks. "
                    "Video block will be skipped. Consider using GeminiRuntime for video.",
                    UserWarning,
                    stacklevel=3,
                )
            elif btype == "file":
                warnings.warn(
                    "ClaudeCodeRuntime does not support file/PDF content blocks. "
                    "File block will be skipped. Use AnthropicRuntime API directly for PDF support.",
                    UserWarning,
                    stacklevel=3,
                )
            else:
                filtered_content.append(block)

        content = filtered_content

        with self._lock:
            self._ensure_process()

            # Context-aware compact: if the previous turn pushed us past the
            # configured threshold, compact before sending the new prompt.
            # Falls back to a full process restart if compact fails.
            # _last_context_tokens is updated from compact_boundary.post_tokens
            # during compact(), so no manual reset needed here.
            if self._should_compact():
                try:
                    self.compact()
                except Exception:
                    self._restart_process()

            # Build Anthropic-format content blocks
            anthropic_content = []
            for block in content:
                if block.get("type") == "text":
                    anthropic_content.append({"type": "text", "text": block["text"]})
                elif block.get("type") == "image":
                    img_block = self._encode_image(block)
                    if img_block:
                        anthropic_content.append(img_block)
                elif "text" in block:
                    anthropic_content.append({"type": "text", "text": block["text"]})

            if response_format:
                anthropic_content.append({
                    "type": "text",
                    "text": f"\n\nRespond with ONLY valid JSON matching: {json.dumps(response_format)}",
                })

            # Build stream-json message
            message = json.dumps({
                "type": "user",
                "message": {
                    "role": "user",
                    "content": anthropic_content,
                },
            })

            try:
                self._proc.stdin.write(message + "\n")
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError):
                # Process died — restart and retry
                self._proc = None
                self._ensure_process()
                self._proc.stdin.write(message + "\n")
                self._proc.stdin.flush()

            # Read response lines until we get a result
            reply, events = self._read_response()
            self._turn_count += 1

            # Save intermediate events to log (not to LLM context)
            self._save_turn_log(events)

            # Compact context periodically to prevent bloat
            if self._compact_every and self._turn_count % self._compact_every == 0:
                self.compact()

            return reply

    def _read_response(self) -> tuple[str, list]:
        """Read lines from stdout until we get a result message.

        The timeout is per-line, not total. As long as the process keeps
        producing output (e.g., tool_use events during interactive mode),
        the deadline is extended. Timeout only fires when the process goes
        silent for self.timeout seconds.

        Returns:
            (result_text, events) — the final reply text and a list of
            all intermediate events (for logging, not for LLM context).
        """
        deadline = time.time() + self.timeout
        result_text = None
        events = []
        start_time = time.time()
        # `system` event at the start of a stream carries the authoritative
        # model id for the turn. We capture it so the result handler can pick
        # the correct entry out of `modelUsage` (which often contains 2 keys:
        # main model + the Haiku router-helper CLI uses for auxiliary tasks).
        primary_model: Optional[str] = None
        # If a `compact_boundary` event appears in this stream, the modelUsage
        # in the result reflects the compact operation's own consumption (it
        # reads the whole pre-compact session to generate the summary), NOT
        # the post-compact session size. Capture the real post size here so
        # the result handler can override _last_context_tokens correctly.
        post_compact_tokens: Optional[int] = None

        while time.time() < deadline:
            # Check if process is still alive
            if self._proc.poll() is not None:
                stderr = self._proc.stderr.read() if self._proc.stderr else ""
                raise RuntimeError(
                    f"Claude Code CLI process exited unexpectedly "
                    f"(code {self._proc.returncode}): {stderr[:500]}"
                )

            # Read one line with timeout
            line = self._read_line_with_timeout(deadline - time.time())
            if line is None:
                continue

            line = line.strip()
            if not line:
                continue

            # Got output — reset deadline (process is alive and working)
            deadline = time.time() + self.timeout

            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg_type = data.get("type", "")
            elapsed = round(time.time() - start_time, 1)

            # Collect event for logging
            event = {"type": msg_type, "elapsed": elapsed}

            if msg_type == "result":
                result_text = data.get("result", "")
                usage = data.get("usage", {})
                # Anthropic API reports input_tokens as non-cached only.
                # Normalize to total input (like OpenAI) for consistent display.
                raw_in = usage.get("input_tokens", 0)
                cache_read = usage.get("cache_read_input_tokens", 0)
                cache_create = usage.get("cache_creation_input_tokens", 0)
                # input_tokens = total input (raw + cache_read + cache_create)
                # cache_read = only actual cache hits (NOT cache_create,
                #   which are new tokens written to cache — cost MORE than regular input)
                usage_dict = {
                    "input_tokens": raw_in + cache_read + cache_create,
                    "output_tokens": usage.get("output_tokens", 0),
                    "cache_read": cache_read,
                    "cache_create": cache_create,
                }
                self.last_usage = usage_dict

                # Capture contextWindow + accumulated context size from
                # modelUsage. This is the authoritative signal for compact:
                # CLI reports the model's real context window and how many
                # tokens the last turn sent. Used by _should_compact().
                #
                # modelUsage can contain two keys: the requested model AND
                # the Haiku router helper CLI uses internally. Prefer the
                # entry whose key matches `primary_model` (from the `system`
                # event). Fall back to the key containing "haiku" last so
                # we never mistake the helper for the main model.
                mu_all = data.get("modelUsage", {}) or {}
                picked = None
                if primary_model:
                    for mname in mu_all:
                        if mname == primary_model or primary_model in mname:
                            picked = mname
                            break
                if picked is None and mu_all:
                    non_haiku = [k for k in mu_all if "haiku" not in k.lower()]
                    picked = non_haiku[0] if non_haiku else next(iter(mu_all))
                if picked:
                    mu = mu_all[picked]
                    win = mu.get("contextWindow")
                    if win:
                        self._context_window_tokens = win
                    self._resolved_model_id = picked
                # modelUsage is CUMULATIVE across the whole subprocess session,
                # so it can't be used for a per-turn context-size signal.
                # The top-level `usage` dict reports per-turn tokens — use it.
                # After /compact, the result event's `usage` is all-zeros (the
                # compact itself had no user-visible response); override with
                # compact_boundary.post_tokens in that case.
                if post_compact_tokens is not None:
                    self._last_context_tokens = post_compact_tokens
                else:
                    self._last_context_tokens = usage_dict["input_tokens"]

                event["result"] = result_text[:200]
                event["usage"] = usage_dict
                event["duration_ms"] = data.get("duration_ms", 0)
                event["num_turns"] = data.get("num_turns", 0)
                events.append(event)
                return result_text, events

            if msg_type == "assistant" and "message" in data:
                msg = data["message"]
                if isinstance(msg, dict) and "content" in msg:
                    for block in msg["content"]:
                        if isinstance(block, dict):
                            if block.get("type") == "text":
                                event_text = {"type": "text", "elapsed": elapsed,
                                              "text": block["text"][:200]}
                                events.append(event_text)
                                result_text = block["text"]
                                if self.on_stream:
                                    try:
                                        self.on_stream(event_text)
                                    except Exception:
                                        pass
                            elif block.get("type") == "tool_use":
                                event_tool = {"type": "tool_use", "elapsed": elapsed,
                                              "tool": block.get("name", "?"),
                                              "input": str(block.get("input", {}))[:100]}
                                events.append(event_tool)
                                if self.on_stream:
                                    try:
                                        self.on_stream(event_tool)
                                    except Exception:
                                        pass
                    continue

            # `system` event — first stream event; carries init model id.
            if msg_type == "system" and primary_model is None:
                primary_model = data.get("model")

            # `compact_boundary` event — emitted when /compact runs. Its
            # compact_metadata.post_tokens is the authoritative post-compact
            # session size. Save it so the result handler can override the
            # otherwise-misleading modelUsage numbers.
            if msg_type == "system" and data.get("subtype") == "compact_boundary":
                meta = data.get("compact_metadata") or {}
                post = meta.get("post_tokens")
                if post is not None:
                    post_compact_tokens = int(post)
                event["compact_pre"] = meta.get("pre_tokens")
                event["compact_post"] = post

            # Other events (rate_limit, system, etc.)
            events.append(event)

        raise TimeoutError(f"Claude Code CLI timed out (no output for {self.timeout}s)")

    def _save_turn_log(self, events: list):
        """Save intermediate events from a turn to a log file."""
        try:
            log_dir = os.path.join(os.path.dirname(__file__), "..", "logs")
            os.makedirs(log_dir, exist_ok=True)
            log_file = os.path.join(log_dir, "claude_code_turns.jsonl")
            entry = {
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "turn": self._turn_count,
                "events": events,
            }
            with open(log_file, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass  # Never fail for logging

    def _read_stdout_loop(self):
        """Persistent thread that reads stdout lines into a queue."""
        try:
            while self._proc and self._proc.poll() is None:
                line = self._proc.stdout.readline()
                if not line:
                    break
                self._stdout_queue.put(line)
        except Exception:
            pass

    def _read_line_with_timeout(self, remaining: float) -> Optional[str]:
        """Read a single line from the stdout queue with timeout."""
        import queue
        try:
            return self._stdout_queue.get(timeout=min(remaining, 5.0))
        except queue.Empty:
            return None

    def _drain_stderr(self):
        """Read and discard stderr to prevent pipe buffer deadlock."""
        try:
            while self._proc and self._proc.poll() is None:
                line = self._proc.stderr.readline()
                if not line:
                    break
        except Exception:
            pass

    def _compact_threshold_tokens(self) -> Optional[int]:
        """Token count that triggers `/compact`. None if we don't yet know
        the model's context window (haven't received a result event yet)."""
        w = self._context_window_tokens
        if w is None:
            return None
        # Rule: ≤500K window → compact at ratio (default 80%).
        #       >500K window → cap at compact_cap_tokens (default 500K).
        if w <= self._compact_cap_tokens:
            return int(w * self._compact_ratio)
        return self._compact_cap_tokens

    def _should_compact(self) -> bool:
        """True if accumulated context exceeds the model-aware threshold."""
        thr = self._compact_threshold_tokens()
        if thr is None:
            return False
        return self._last_context_tokens >= thr

    def compact(self, threshold_tokens: Optional[int] = None) -> bool:
        """Send /compact to compress the conversation context.

        Claude Code slash command that summarizes prior messages to free up
        context window space. Keeps the session alive without restarting
        the process. Callable from outside (e.g., GUI agent loops that
        want to compress at specific step boundaries).

        Args:
            threshold_tokens: If given, skip when current context is below
                this size (returns False). If None, always run.

        Returns:
            True if compact ran, False if skipped due to threshold.
        """
        if threshold_tokens is not None and self._last_context_tokens < threshold_tokens:
            return False
        import sys as _sys
        t0 = time.time()
        try:
            tokens_before = self._last_context_tokens
            compact_msg = json.dumps({
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "/compact"}],
                },
            })
            self._proc.stdin.write(compact_msg + "\n")
            self._proc.stdin.flush()
            result_text, events = self._read_response()
            elapsed = time.time() - t0
            event_types = [e.get("type", "?") for e in events[:8]]
            print(
                f"[compact] {elapsed:.1f}s | tokens {tokens_before}->"
                f"{self._last_context_tokens} | "
                f"result={result_text[:120]!r} | events={event_types}",
                file=_sys.stderr,
            )
        except Exception as e:
            print(f"[compact] ERROR after {time.time()-t0:.1f}s: {e}", file=_sys.stderr)
        return True

    def _encode_image(self, block: dict) -> Optional[dict]:
        """Convert an image content block to Anthropic base64 format."""
        if "data" in block:
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": block.get("media_type", "image/png"),
                    "data": block["data"],
                },
            }

        if "path" in block:
            path = block["path"]
            media_type = mimetypes.guess_type(path)[0] or "image/png"
            try:
                with open(path, "rb") as f:
                    data = base64.b64encode(f.read()).decode("utf-8")
                return {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": data,
                    },
                }
            except FileNotFoundError:
                return None

        return None

    def _restart_process(self):
        """Kill the current process so a new one starts on next _call().

        Unlike close(), this does NOT mark the runtime as closed —
        it just restarts the underlying CLI process.
        """
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None
        self._turn_count = 0

    def close(self):
        """Kill the Claude Code process and release resources."""
        self._restart_process()
        super().close()
